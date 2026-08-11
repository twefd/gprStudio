"""Scene model for gprStudio.

A :class:`Scene` describes a 2D vertical cross-section: a stack of horizontal
material layers (top = surface) plus a list of embedded objects (rebar rows,
single bars, conduits/ducts, voids/delaminations).  All positions are given in
metres, with ``x`` the horizontal scan direction and ``depth`` measured
downward from the surface.  :mod:`gpr_studio.infile` converts a Scene plus a
:class:`Survey` into a gprMax ``.in`` file.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Layer:
    """A horizontal material layer, ordered from the surface downward."""

    material: str          # material name (key into the material library)
    thickness_m: float     # vertical thickness; the LAST layer is a half-space


@dataclass
class RebarRow:
    """A row of parallel reinforcement bars (perpendicular to the section)."""

    depth_m: float         # cover depth to bar centre
    diameter_m: float      # bar diameter
    spacing_m: float       # centre-to-centre spacing
    count: int             # number of bars
    x_start_m: float       # x of the first bar centre
    material: str = "steel"
    label: str = "Rebar row"


@dataclass
class Bar:
    """A single cylindrical object: dowel, conduit, duct or post-tension sheath."""

    x_m: float
    depth_m: float
    diameter_m: float
    material: str          # e.g. steel, pvc, water
    grout_material: str | None = None   # optional filled core (e.g. grout in a duct)
    grout_diameter_m: float = 0.0
    label: str = "Bar / conduit"


@dataclass
class VoidBox:
    """A rectangular defect: air/water void, delamination or honeycomb zone."""

    x_m: float             # left edge
    depth_m: float         # depth to the TOP of the box
    width_m: float
    height_m: float
    material: str = "air"
    label: str = "Void"


@dataclass
class DiagonalVoid:
    """An angled crack / void: a thin rotated rectangle (built from triangles).

    Defined by one end point plus a length, an angle and an aperture (width).
    ``angle_deg`` is measured from horizontal; positive tilts down-to-the-right.
    """

    x_m: float             # x of the start end (centre of that end face)
    depth_m: float         # depth of the start end
    length_m: float        # length of the crack
    angle_deg: float       # tilt from horizontal (deg); + = downward to the right
    aperture_m: float      # crack width / thickness
    material: str = "air"
    label: str = "Diagonal crack"


@dataclass
class OvalRow:
    """A row of oval (elliptical) voids — e.g. hollow-core slab channels.

    Each oval is approximated by a fan of triangles. ``count``/``spacing`` allow
    a repeated row (a single oval is count = 1).
    """

    cx_start_m: float      # x of the first oval centre
    depth_m: float         # depth of the oval centre
    width_m: float         # full horizontal diameter
    height_m: float        # full vertical diameter
    count: int = 1
    spacing_m: float = 0.0  # centre-to-centre spacing
    material: str = "air"
    label: str = "Oval void"


@dataclass
class Survey:
    """Acquisition parameters that drive the source/receiver and B-scan sweep."""

    equipment_key: str
    center_freq_hz: float
    tx_rx_offset_m: float
    scan_length_m: float          # total horizontal travel of the antenna
    trace_step_m: float           # distance between successive traces
    max_depth_m: float            # deepest feature of interest (sets time window)
    dx_m: float                   # spatial discretisation (dx = dy = dz)
    pml_cells: int = 10

    @property
    def step_cells(self) -> int:
        """Trace step expressed in whole grid cells (gprMax steps in cells).

        The antenna cannot be moved less than one cell, so a requested trace
        step finer than the cell size is rounded up to a single cell.
        """
        if self.dx_m <= 0:
            return 1
        return max(1, int(round(self.trace_step_m / self.dx_m)))

    @property
    def trace_step_eff_m(self) -> float:
        """Actual (cell-aligned) trace step that gprMax will use."""
        return self.step_cells * self.dx_m

    @property
    def num_traces(self) -> int:
        s = self.trace_step_eff_m
        if s <= 0:
            return 1
        return max(1, int(round(self.scan_length_m / s)) + 1)


@dataclass
class Scene:
    """A complete cross-section: layers + embedded objects + metadata."""

    name: str = "concrete_scene"
    title: str = "gprStudio model"
    layers: list[Layer] = field(default_factory=list)
    rebar_rows: list[RebarRow] = field(default_factory=list)
    bars: list[Bar] = field(default_factory=list)
    voids: list[VoidBox] = field(default_factory=list)
    diagonals: list[DiagonalVoid] = field(default_factory=list)
    ovals: list[OvalRow] = field(default_factory=list)

    def total_layer_depth(self) -> float:
        """Sum of all layer thicknesses (the last layer counts once)."""
        return sum(l.thickness_m for l in self.layers)

    def used_materials(self) -> set[str]:
        """Every material name referenced anywhere in the scene."""
        names: set[str] = set()
        for l in self.layers:
            names.add(l.material)
        for r in self.rebar_rows:
            names.add(r.material)
        for b in self.bars:
            names.add(b.material)
            if b.grout_material:
                names.add(b.grout_material)
        for v in self.voids:
            names.add(v.material)
        for d in self.diagonals:
            names.add(d.material)
        for o in self.ovals:
            names.add(o.material)
        return names
