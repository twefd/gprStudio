"""Convert a Scene + Survey into a gprMax ``.in`` input file.

Coordinate mapping (2D cross-section, gprMax uses metres with the y-axis
pointing UP and a domain one cell thick in z):

* ``x`` is the horizontal scan direction.
* ``depth`` is measured downward from the surface; it maps to
  ``y = y_surface - depth``.
* the medium fills ``y = 0 .. y_surface``; free space (air) fills
  ``y_surface .. domain_top``; the antenna sits on the surface.

A PML plus a physical pad surrounds the region of interest so targets never sit
inside the absorbing boundary.
"""

from __future__ import annotations

import math

from .materials import Material
from .model import Scene, Survey, DiagonalVoid, OvalRow

C0 = 299792458.0  # speed of light [m/s]


# --------------------------------------------------------------------------- #
# Shape geometry helpers (object frame: x from 0, depth downward)
# --------------------------------------------------------------------------- #
def diagonal_corners(d: DiagonalVoid) -> list[tuple[float, float]]:
    """Four (x, depth) corners of a diagonal crack's rotated rectangle."""
    a = math.radians(d.angle_deg)
    ax, ad = math.cos(a), math.sin(a)     # along-length direction
    px, pd = -math.sin(a), math.cos(a)    # perpendicular (aperture) direction
    h = d.aperture_m / 2.0
    sx, sd = d.x_m, d.depth_m
    ex, ed = sx + d.length_m * ax, sd + d.length_m * ad
    return [
        (sx + h * px, sd + h * pd),
        (sx - h * px, sd - h * pd),
        (ex - h * px, ed - h * pd),
        (ex + h * px, ed + h * pd),
    ]


def oval_centers(o: OvalRow) -> list[tuple[float, float]]:
    """(x, depth) centre of each oval in the row."""
    return [(o.cx_start_m + k * o.spacing_m, o.depth_m)
            for k in range(max(1, o.count))]


# --------------------------------------------------------------------------- #
# Discretisation / timing helpers
# --------------------------------------------------------------------------- #
def _min_velocity(scene: Scene, materials: dict[str, Material]) -> float:
    """Slowest EM velocity among the materials used (highest permittivity)."""
    er_max = 1.0
    for name in scene.used_materials():
        mat = materials.get(name)
        if mat and not mat.is_pec:
            er_max = max(er_max, mat.er)
    return C0 / (er_max ** 0.5)


def suggest_dx(center_freq_hz: float, scene: Scene,
               materials: dict[str, Material],
               cells_per_wavelength: int = 20) -> float:
    """Suggested cell size so the shortest wavelength is well sampled.

    Uses the slowest medium and the centre frequency, then clamps to a
    sensible 1-5 mm range for concrete work.
    """
    v_min = _min_velocity(scene, materials)
    wavelength = v_min / center_freq_hz
    dx = wavelength / cells_per_wavelength
    return float(min(0.005, max(0.001, dx)))


def suggest_time_window(max_depth_m: float, scene: Scene,
                        materials: dict[str, Material],
                        center_freq_hz: float) -> float:
    """Two-way travel time to the deepest feature, with headroom."""
    v_min = _min_velocity(scene, materials)
    depth = max(max_depth_m, scene.total_layer_depth())
    two_way = 2.0 * depth / v_min
    # 1.4x headroom for scattering/ringing + a couple of pulse widths.
    return two_way * 1.4 + 2.0 / center_freq_hz


def content_depth(scene: Scene, survey: Survey) -> float:
    """Depth (m) the medium must span to contain every feature + margin."""
    deepest = max(survey.max_depth_m, scene.total_layer_depth())
    for r in scene.rebar_rows:
        deepest = max(deepest, r.depth_m + r.diameter_m)
    for b in scene.bars:
        deepest = max(deepest, b.depth_m + b.diameter_m)
    for v in scene.voids:
        deepest = max(deepest, v.depth_m + v.height_m)
    for d in scene.diagonals:
        deepest = max(deepest, max(dep for _, dep in diagonal_corners(d)))
    for o in scene.ovals:
        deepest = max(deepest, o.depth_m + o.height_m / 2)
    return deepest + 0.05  # 5 cm clearance below the deepest feature


def _min_object_x(scene: Scene) -> float:
    """Leftmost x-extent of any embedded object (object frame, from x=0)."""
    lefts = []
    for r in scene.rebar_rows:
        lefts.append(r.x_start_m - r.diameter_m / 2)
    for b in scene.bars:
        lefts.append(b.x_m - b.diameter_m / 2)
    for v in scene.voids:
        lefts.append(v.x_m)
    for d in scene.diagonals:
        lefts.extend(x for x, _ in diagonal_corners(d))
    for o in scene.ovals:
        lefts.extend(cx - o.width_m / 2 for cx, _ in oval_centers(o))
    return min(lefts) if lefts else 0.0


def _max_object_x(scene: Scene) -> float:
    """Rightmost x-extent of any embedded object (object frame, from x=0)."""
    right = 0.0
    for r in scene.rebar_rows:
        right = max(right, r.x_start_m + (r.count - 1) * r.spacing_m
                    + r.diameter_m / 2)
    for b in scene.bars:
        right = max(right, b.x_m + b.diameter_m / 2)
    for v in scene.voids:
        right = max(right, v.x_m + v.width_m)
    for d in scene.diagonals:
        right = max([right] + [x for x, _ in diagonal_corners(d)])
    for o in scene.ovals:
        right = max([right] + [cx + o.width_m / 2 for cx, _ in oval_centers(o)])
    return right


def full_scan_length(scene: Scene, margin_m: float = 0.05) -> float:
    """Scan length so the antenna travels across every object, plus margin.

    The transmitter starts at object-frame x = 0 and steps to the right, so it
    passes over the rightmost object once the scan length reaches that object's
    x-extent.  A margin is added so the last target is imaged with some run-out.
    """
    return _max_object_x(scene) + margin_m


# --------------------------------------------------------------------------- #
# Geometry layout
# --------------------------------------------------------------------------- #
class Layout:
    """Computed domain dimensions and the surface position (all in metres)."""

    def __init__(self, scene: Scene, survey: Survey,
                 materials: dict[str, Material]):
        self.dx = survey.dx_m
        pad = survey.pml_cells * self.dx + max(0.02, 5 * self.dx)
        self.pad = pad

        # --- horizontal ---
        offset = survey.tx_rx_offset_m
        self.x_antenna_start = pad
        self.scan_length = survey.scan_length_m
        # gprMax steps the antenna in whole cells and guards that
        # start + step_cells * num_traces stays inside the domain (it uses the
        # full trace count, not count-1). Size the domain so the receiver -- the
        # rightmost moving point -- still fits after all its steps, and so every
        # embedded object fits too.
        stepped_right = offset + survey.trace_step_eff_m * survey.num_traces
        content_right = max(stepped_right, _max_object_x(scene))
        self.width = pad + content_right + pad

        # --- vertical ---
        self.depth = content_depth(scene, survey)
        self.y_surface = pad + self.depth
        self.height = self.y_surface + pad

        # z is one cell thick (2D)
        self.dz = self.dx

    def y_of_depth(self, depth_m: float) -> float:
        return self.y_surface - depth_m


# --------------------------------------------------------------------------- #
# .in generation
# --------------------------------------------------------------------------- #
def generate(scene: Scene, survey: Survey,
             materials: dict[str, Material]) -> str:
    """Return the full text of a gprMax ``.in`` file for this scene."""
    lay = Layout(scene, survey, materials)
    dx = lay.dx
    z0 = 0.0
    zt = lay.dz  # object thickness in z (one cell)

    def fmt(v: float) -> str:
        return f"{v:.6g}"

    L: list[str] = []
    L.append(f"#title: {scene.title}")
    L.append("")
    L.append("## --- Discretisation & domain (auto-generated by gprStudio) ---")
    L.append(f"#domain: {fmt(lay.width)} {fmt(lay.height)} {fmt(lay.dz)}")
    L.append(f"#dx_dy_dz: {fmt(dx)} {fmt(dx)} {fmt(lay.dz)}")
    tw = suggest_time_window(survey.max_depth_m, scene, materials,
                             survey.center_freq_hz)
    L.append(f"#time_window: {fmt(tw)}")
    # 2D model is one cell thick in z, so PML must be 0 on the z faces.
    # Order: x0 y0 z0 xmax ymax zmax.
    p = survey.pml_cells
    L.append(f"#pml_cells: {p} {p} 0 {p} {p} 0")
    L.append("")

    # --- Materials -----------------------------------------------------------
    L.append("## --- Materials ---")
    for name in sorted(scene.used_materials()):
        mat = materials.get(name)
        if mat is None or mat.is_pec:
            continue
        L.append(mat.material_line())
    L.append("")

    # --- Source & receiver ---------------------------------------------------
    eq_note = "SFCW system modelled as an equivalent Ricker pulse"
    L.append(f"## --- Source & receiver ({eq_note}) ---")
    L.append(f"#waveform: ricker 1 {fmt(survey.center_freq_hz)} gpr_pulse")
    x_tx = lay.x_antenna_start
    x_rx = lay.x_antenna_start + survey.tx_rx_offset_m
    ys = lay.y_surface
    L.append(f"#hertzian_dipole: z {fmt(x_tx)} {fmt(ys)} {fmt(z0)} gpr_pulse")
    L.append(f"#rx: {fmt(x_rx)} {fmt(ys)} {fmt(z0)}")
    # Write the cell-aligned step so gprMax's cell rounding is a no-op and the
    # trace count stays consistent with the actual antenna travel.
    L.append(f"#src_steps: {fmt(survey.trace_step_eff_m)} 0 0")
    L.append(f"#rx_steps: {fmt(survey.trace_step_eff_m)} 0 0")
    L.append("")

    # --- Layers (top-down); last layer is a half-space to the domain bottom --
    L.append("## --- Layers ---")
    depth_cursor = 0.0
    n_layers = len(scene.layers)
    for i, layer in enumerate(scene.layers):
        top_depth = depth_cursor
        if i == n_layers - 1:
            bottom_depth = lay.depth + lay.pad  # extend through bottom PML
        else:
            bottom_depth = depth_cursor + layer.thickness_m
        y_top = lay.y_of_depth(bottom_depth)   # smaller y
        y_bot = lay.y_of_depth(top_depth)       # larger y
        L.append(
            f"#box: 0 {fmt(max(0.0, y_top))} {fmt(z0)} "
            f"{fmt(lay.width)} {fmt(y_bot)} {fmt(zt)} {layer.material}"
        )
        depth_cursor += layer.thickness_m
    L.append("")

    # --- Voids / delaminations / honeycomb -----------------------------------
    if scene.voids:
        L.append("## --- Voids / delaminations ---")
        for v in scene.voids:
            y_top = lay.y_of_depth(v.depth_m + v.height_m)
            y_bot = lay.y_of_depth(v.depth_m)
            x1 = lay.x_antenna_start + v.x_m
            x2 = x1 + v.width_m
            L.append(
                f"#box: {fmt(x1)} {fmt(y_top)} {fmt(z0)} "
                f"{fmt(x2)} {fmt(y_bot)} {fmt(zt)} {v.material}"
            )
        L.append("")

    # --- Bars / conduits / ducts ---------------------------------------------
    if scene.bars:
        L.append("## --- Bars / conduits / ducts ---")
        for b in scene.bars:
            xc = lay.x_antenna_start + b.x_m
            yc = lay.y_of_depth(b.depth_m)
            L.append(
                f"#cylinder: {fmt(xc)} {fmt(yc)} {fmt(z0)} "
                f"{fmt(xc)} {fmt(yc)} {fmt(zt)} {fmt(b.diameter_m / 2)} "
                f"{_mat_token(b.material, materials)}"
            )
            if b.grout_material and b.grout_diameter_m > 0:
                L.append(
                    f"#cylinder: {fmt(xc)} {fmt(yc)} {fmt(z0)} "
                    f"{fmt(xc)} {fmt(yc)} {fmt(zt)} "
                    f"{fmt(b.grout_diameter_m / 2)} "
                    f"{_mat_token(b.grout_material, materials)}"
                )
        L.append("")

    # --- Rebar rows ----------------------------------------------------------
    if scene.rebar_rows:
        L.append("## --- Rebar ---")
        for row in scene.rebar_rows:
            yc = lay.y_of_depth(row.depth_m)
            token = _mat_token(row.material, materials)
            for k in range(row.count):
                xc = lay.x_antenna_start + row.x_start_m + k * row.spacing_m
                L.append(
                    f"#cylinder: {fmt(xc)} {fmt(yc)} {fmt(z0)} "
                    f"{fmt(xc)} {fmt(yc)} {fmt(zt)} "
                    f"{fmt(row.diameter_m / 2)} {token}"
                )
        L.append("")

    # Emit an xy-plane triangular prism (one cell thick in z) from three
    # (x, depth) object-frame vertices.
    def tri(v1, v2, v3, mat_token) -> str:
        parts = []
        for (vx, vd) in (v1, v2, v3):
            parts.append(f"{fmt(lay.x_antenna_start + vx)} "
                         f"{fmt(lay.y_of_depth(vd))} {fmt(z0)}")
        return f"#triangle: {' '.join(parts)} {fmt(zt)} {mat_token}"

    # --- Diagonal cracks / voids (rotated rectangles = 2 triangles) ----------
    if scene.diagonals:
        L.append("## --- Diagonal cracks / voids ---")
        for d in scene.diagonals:
            c = diagonal_corners(d)
            tok = _mat_token(d.material, materials)
            L.append(tri(c[0], c[1], c[2], tok))
            L.append(tri(c[0], c[2], c[3], tok))
        L.append("")

    # --- Oval voids (hollow-core channels / ducts) = triangle fan -----------
    if scene.ovals:
        L.append("## --- Oval voids (hollow-core / ducts) ---")
        segments = 28
        for o in scene.ovals:
            tok = _mat_token(o.material, materials)
            ax, ay = o.width_m / 2.0, o.height_m / 2.0
            for (cx, cd) in oval_centers(o):
                pts = [(cx + ax * math.cos(2 * math.pi * i / segments),
                        cd + ay * math.sin(2 * math.pi * i / segments))
                       for i in range(segments)]
                for i in range(segments):
                    L.append(tri((cx, cd), pts[i], pts[(i + 1) % segments], tok))
        L.append("")

    return "\n".join(L) + "\n"


def _mat_token(name: str, materials: dict[str, Material]) -> str:
    """Return the gprMax material token: 'pec' for metal, else the name."""
    mat = materials.get(name)
    if mat is not None and mat.is_pec:
        return "pec"
    return name
