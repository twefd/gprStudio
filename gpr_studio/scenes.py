"""Save / load geometry settings and built-in geometry presets.

A "scene file" is a small JSON document capturing the app's editable state in
its UI units (cm / mm / GHz): the geometry (layers + objects), the survey
settings, and the material library. This makes save/import an exact round-trip
and lets users share setups.

Presets return the same session-state dict so "load preset" and "import file"
share one code path.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .materials import Material, default_library

CM = 0.01
MM = 0.001
SCHEMA_VERSION = 1

# Curated presets live as .gprstudio.json files in their own presets/ folder,
# kept separate from run output. Scenes the user saves into projects/ are also
# discovered (the "new projects"), so both appear in the dropdown.
_ROOT = Path(__file__).resolve().parent
PRESETS_DIR = _ROOT / "presets"
PROJECTS_DIR = _ROOT / "projects"

# Session-state keys that make up a saved scene.
META_KEYS = ["project_name", "title"]
SURVEY_KEYS = [
    "equipment_key", "center_freq_ghz", "tx_rx_offset_mm", "scan_length_cm",
    "trace_step_mm", "max_depth_cm", "pml_cells", "dx_override_mm",
]
OBJECT_KEYS = ["layers", "rebar", "bars", "voids", "diagonals", "ovals"]


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #
def to_json(ss: Any) -> str:
    """Serialise the relevant session state to a JSON string."""
    data: dict[str, Any] = {"schema": SCHEMA_VERSION}
    for k in META_KEYS + SURVEY_KEYS + OBJECT_KEYS:
        data[k] = ss[k] if k in ss else _get(ss, k)
    data["materials"] = [
        {"name": m.name, "label": m.label, "er": m.er, "sigma": m.sigma,
         "mu_r": m.mu_r, "sigma_star": m.sigma_star, "color": m.color,
         "note": m.note, "is_pec": m.is_pec}
        for m in _get(ss, "materials").values()
    ]
    return json.dumps(data, indent=2)


def from_json(text: str) -> dict[str, Any]:
    """Parse a scene JSON into a dict of session-state updates.

    Unknown/missing fields are tolerated; only recognised keys are returned.
    Raises ValueError on invalid JSON or wrong shape.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not a valid scene file: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Scene file must be a JSON object.")

    updates: dict[str, Any] = {}
    for k in META_KEYS + SURVEY_KEYS + OBJECT_KEYS:
        if k in data:
            updates[k] = data[k]
    if "materials" in data and isinstance(data["materials"], list):
        lib: dict[str, Material] = {}
        for r in data["materials"]:
            try:
                lib[r["name"]] = Material(
                    name=r["name"], label=r.get("label", r["name"]),
                    er=float(r["er"]), sigma=float(r["sigma"]),
                    mu_r=float(r.get("mu_r", 1.0)),
                    sigma_star=float(r.get("sigma_star", 0.0)),
                    color=r.get("color", "#cccccc"), note=r.get("note", ""),
                    is_pec=bool(r.get("is_pec", False)))
            except (KeyError, TypeError, ValueError):
                continue
        if lib:
            updates["materials"] = lib
    return updates


def _get(ss: Any, key: str) -> Any:
    """Read a key from a Streamlit session_state or a plain dict."""
    try:
        return ss[key]
    except Exception:
        return getattr(ss, key)


def import_text(text: str) -> dict[str, Any]:
    """Import a pasted string: either a scene JSON or a gprMax ``.in`` file."""
    t = text.strip()
    if not t:
        raise ValueError("Nothing to import — paste a scene JSON or a gprMax "
                         "input file.")
    if t[0] == "{":
        return from_json(t)
    return parse_infile(t)


def parse_infile(text: str) -> dict[str, Any]:
    """Best-effort parse of a gprMax ``.in`` file into session-state updates.

    Reconstructs the survey grid, materials, full-width boxes as layers, other
    boxes as voids/blocks, and cylinders as rebar rows / bars. Triangle-based
    shapes (diagonal cracks, ovals) cannot be reliably reversed and are skipped
    with a note. Works best on files this app generated, but tolerates general
    2D files.
    """
    dx = domain = src = rx = None
    srcstep = freq = None
    pml, title = 10, None
    boxes, cyls, tris, mats = [], [], 0, {}

    for raw in text.splitlines():
        ln = raw.strip()
        if not ln or ln.startswith("##") or not ln.startswith("#"):
            continue
        if ln.lower().startswith("#title:"):
            title = ln.split(":", 1)[1].strip()
            continue
        parts = ln.replace(":", " ").split()
        key, vals = parts[0].lstrip("#"), parts[1:]
        try:
            if key == "dx_dy_dz":
                dx = float(vals[0])
            elif key == "domain":
                domain = (float(vals[0]), float(vals[1]), float(vals[2]))
            elif key == "pml_cells":
                pml = int(float(vals[0]))
            elif key == "waveform":
                freq = float(vals[2])
            elif key == "hertzian_dipole":
                src = (float(vals[1]), float(vals[2]))
            elif key == "rx":
                rx = (float(vals[0]), float(vals[1]))
            elif key == "src_steps":
                srcstep = float(vals[0])
            elif key == "material":
                mats[vals[4]] = (float(vals[0]), float(vals[1]),
                                 float(vals[2]), float(vals[3]))
            elif key == "box":
                boxes.append((float(vals[0]), float(vals[1]),
                              float(vals[3]), float(vals[4]), vals[6]))
            elif key == "cylinder":
                cyls.append((float(vals[0]), float(vals[1]),
                             float(vals[6]), vals[7]))
            elif key == "triangle":
                tris += 1
        except (IndexError, ValueError):
            continue

    if dx is None or domain is None:
        raise ValueError("Not a recognisable gprMax input file "
                         "(missing #domain / #dx_dy_dz).")

    domain_x = domain[0]
    x0 = src[0] if src else 0.0
    y_surface = (src[1] if src else
                 max([b[3] for b in boxes if b[0] <= dx * 1.5
                      and b[2] >= domain_x - dx * 1.5] or [domain[1] * 0.8]))
    offset = (rx[0] - src[0]) if (rx and src) else 0.04

    def matname(tok: str) -> str:
        return "steel" if tok == "pec" else tok

    # Materials: start from defaults, overlay/add parsed ones.
    lib = default_library()
    for name, (er, sig, mur, sst) in mats.items():
        if name in lib:
            m = lib[name]
            m.er, m.sigma, m.mu_r, m.sigma_star = er, sig, mur, sst
        else:
            lib[name] = Material(name=name, label=name, er=er, sigma=sig,
                                 mu_r=mur, sigma_star=sst, color="#cccccc",
                                 note="imported")

    # Boxes -> layers (full width) or voids/blocks.
    layers_raw, voids = [], []
    for (x1, y1, x2, y2, tok) in boxes:
        full = x1 <= dx * 1.5 and x2 >= domain_x - dx * 1.5
        if full:
            layers_raw.append((y_surface - y2, y_surface - y1, matname(tok)))
        else:
            voids.append({"label": "Imported",
                          "x_cm": round((x1 - x0) / CM, 2),
                          "depth_cm": round((y_surface - y2) / CM, 2),
                          "width_cm": round((x2 - x1) / CM, 2),
                          "height_cm": round((y2 - y1) / CM, 2),
                          "material": matname(tok)})
    layers_raw.sort(key=lambda t: t[0])
    layers = [{"material": mat, "thickness_cm": round(max(0.5, (bot - top) / CM), 2)}
              for (top, bot, mat) in layers_raw]

    # Cylinders -> rebar rows (uniformly spaced groups) or single bars.
    groups: dict[tuple, list[float]] = defaultdict(list)
    for (xc, yc, rad, tok) in cyls:
        groups[(round(y_surface - yc, 4), round(2 * rad, 4), matname(tok))].append(xc)
    rebar, bars = [], []
    for (depth, dia, mat), xs in groups.items():
        xs = sorted(xs)
        diffs = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
        spacing = (sum(diffs) / len(diffs)) if diffs else 0.0
        uniform = spacing > 0 and all(abs(d - spacing) < 0.3 * spacing for d in diffs)
        if len(xs) >= 2 and uniform:
            rebar.append({"label": "Imported rebar",
                          "depth_cm": round(depth / CM, 2),
                          "diameter_mm": round(dia / MM, 2),
                          "spacing_cm": round(spacing / CM, 2), "count": len(xs),
                          "x_start_cm": round((xs[0] - x0) / CM, 2), "material": mat})
        else:
            for x in xs:
                bars.append({"label": "Imported bar",
                             "x_cm": round((x - x0) / CM, 2),
                             "depth_cm": round(depth / CM, 2),
                             "diameter_mm": round(dia / MM, 2), "material": mat,
                             "grout_material": "", "grout_diameter_mm": 0.0})

    obj_depths = ([v["depth_cm"] + v["height_cm"] for v in voids]
                  + [r["depth_cm"] for r in rebar] + [b["depth_cm"] for b in bars])
    layer_depth = sum(l["thickness_cm"] for l in layers[:-1]) if len(layers) > 1 \
        else (layers[0]["thickness_cm"] if layers else 40.0)
    max_depth_cm = round(max([layer_depth, 20.0] + obj_depths), 1)

    updates: dict[str, Any] = {
        "project_name": (title.split()[0].lower() if title else "imported"),
        "title": title or "Imported model",
        "equipment_key": "gp8000",
        "center_freq_ghz": round((freq or 2.0e9) / 1e9, 3),
        "tx_rx_offset_mm": round(offset / MM, 1),
        "trace_step_mm": round((srcstep or 0.008) / MM, 2),
        "max_depth_cm": max_depth_cm, "pml_cells": pml,
        "dx_override_mm": round(dx / MM, 3),  # preserve the file's exact grid
        "layers": layers, "rebar": rebar, "bars": bars, "voids": voids,
        "diagonals": [], "ovals": [], "materials": lib,
    }
    # Scan length so the antenna covers all imported objects.
    from . import build, infile
    sc = build.scene_from_state(updates)
    updates["scan_length_cm"] = round(infile.full_scan_length(sc) / CM, 1)
    if tris:
        updates["_import_note"] = (
            f"{tris} triangle-based shape(s) (diagonal cracks / ovals) were "
            "skipped — those import only from .gprstudio.json scene files.")
    return updates


# --------------------------------------------------------------------------- #
# Presets
# --------------------------------------------------------------------------- #
def _base_survey(**over: Any) -> dict[str, Any]:
    s = {
        "equipment_key": "gp8000", "center_freq_ghz": 2.0,
        "tx_rx_offset_mm": 40.0, "scan_length_cm": 60.0,
        "trace_step_mm": 8.0, "max_depth_cm": 40.0, "pml_cells": 10,
        "dx_override_mm": 0.0,
    }
    s.update(over)
    return s


def _preset_slab_in_air() -> dict[str, Any]:
    """A reinforced concrete slab suspended in air (top and bottom visible)."""
    d = {"project_name": "slab_in_air", "title": "Concrete slab in air"}
    d.update(_base_survey(max_depth_cm=30.0, scan_length_cm=70.0))
    d["layers"] = [
        {"material": "dry_concrete", "thickness_cm": 20.0},
        {"material": "air", "thickness_cm": 20.0},  # half-space below the slab
    ]
    d["rebar"] = [{"label": "Rebar mat", "depth_cm": 5.0, "diameter_mm": 12.0,
                   "spacing_cm": 15.0, "count": 6, "x_start_cm": 8.0,
                   "material": "steel"}]
    d["bars"], d["voids"], d["diagonals"], d["ovals"] = [], [], [], []
    return d


def _preset_foundation_beam() -> dict[str, Any]:
    """Inverted-T foundation beam with footing, cast in soil."""
    d = {"project_name": "foundation_beam",
         "title": "Foundation beam with footing"}
    d.update(_base_survey(max_depth_cm=75.0, scan_length_cm=100.0,
                          trace_step_mm=10.0))
    d["layers"] = [{"material": "dry_sand", "thickness_cm": 90.0}]  # soil
    # Concrete shapes as generic blocks (VoidBox with a concrete material).
    d["voids"] = [
        {"label": "Footing", "x_cm": 20.0, "depth_cm": 45.0,
         "width_cm": 60.0, "height_cm": 20.0, "material": "dry_concrete"},
        {"label": "Beam stem", "x_cm": 37.5, "depth_cm": 0.0,
         "width_cm": 25.0, "height_cm": 45.0, "material": "dry_concrete"},
    ]
    d["rebar"] = [
        {"label": "Footing bars", "depth_cm": 55.0, "diameter_mm": 16.0,
         "spacing_cm": 15.0, "count": 4, "x_start_cm": 26.0, "material": "steel"},
        {"label": "Stem bars", "depth_cm": 10.0, "diameter_mm": 16.0,
         "spacing_cm": 15.0, "count": 2, "x_start_cm": 42.0, "material": "steel"},
    ]
    d["bars"], d["diagonals"], d["ovals"] = [], [], []
    return d


def _preset_delamination() -> dict[str, Any]:
    """Concrete slab with a thin horizontal delamination at rebar level."""
    d = {"project_name": "delamination", "title": "Delamination case"}
    d.update(_base_survey(max_depth_cm=35.0, scan_length_cm=70.0))
    d["layers"] = [
        {"material": "dry_concrete", "thickness_cm": 25.0},
        {"material": "dry_sand", "thickness_cm": 20.0},
    ]
    d["rebar"] = [{"label": "Rebar mat", "depth_cm": 5.0, "diameter_mm": 12.0,
                   "spacing_cm": 15.0, "count": 6, "x_start_cm": 8.0,
                   "material": "steel"}]
    d["voids"] = [{"label": "Delamination", "x_cm": 15.0, "depth_cm": 12.0,
                   "width_cm": 40.0, "height_cm": 0.6, "material": "air"}]
    d["bars"], d["diagonals"], d["ovals"] = [], [], []
    return d


def _preset_hollow_core() -> dict[str, Any]:
    """Hollow-core slab (kanaalplaatvloer): concrete slab with oval channels."""
    d = {"project_name": "hollow_core_slab",
         "title": "Hollow-core slab (kanaalplaatvloer)"}
    d.update(_base_survey(max_depth_cm=35.0, scan_length_cm=130.0,
                          trace_step_mm=8.0))
    d["layers"] = [
        {"material": "dry_concrete", "thickness_cm": 26.0},
        {"material": "air", "thickness_cm": 20.0},  # soffit / void below
    ]
    d["ovals"] = [{"label": "Cores", "cx_cm": 12.0, "depth_cm": 13.0,
                   "width_cm": 12.0, "height_cm": 15.0, "count": 8,
                   "spacing_cm": 15.0, "material": "air"}]
    # Prestress strands sit in the concrete webs BETWEEN the cores, near the
    # slab bottom: offset by half the core spacing, one fewer than the cores,
    # and deeper than the core bottoms (core bottom ~20.5 cm, slab ~26 cm).
    d["rebar"] = [{"label": "Prestress strands", "depth_cm": 23.0,
                   "diameter_mm": 12.0, "spacing_cm": 15.0, "count": 7,
                   "x_start_cm": 19.5, "material": "steel"}]
    d["bars"], d["voids"], d["diagonals"] = [], [], []
    return d


def _preset_diagonal_crack() -> dict[str, Any]:
    """Concrete slab with an inclined (diagonal) crack."""
    d = {"project_name": "diagonal_crack", "title": "Diagonal crack in slab"}
    d.update(_base_survey(max_depth_cm=35.0, scan_length_cm=70.0))
    d["layers"] = [
        {"material": "dry_concrete", "thickness_cm": 30.0},
        {"material": "dry_sand", "thickness_cm": 20.0},
    ]
    d["diagonals"] = [{"label": "Diagonal crack", "x_cm": 25.0, "depth_cm": 3.0,
                       "length_cm": 28.0, "angle_deg": 55.0, "aperture_mm": 6.0,
                       "material": "air"}]
    d["rebar"] = [{"label": "Rebar mat", "depth_cm": 5.0, "diameter_mm": 12.0,
                   "spacing_cm": 20.0, "count": 4, "x_start_cm": 8.0,
                   "material": "steel"}]
    d["bars"], d["voids"], d["ovals"] = [], [], []
    return d


# Built-in presets — used only to seed the preset files on first run. After
# that the files are the source of truth and can be freely edited or deleted.
_BUILTIN_BUILDERS: dict[str, Any] = {
    "slab_in_air": _preset_slab_in_air,
    "foundation_beam": _preset_foundation_beam,
    "delamination": _preset_delamination,
    "hollow_core_slab": _preset_hollow_core,
    "diagonal_crack": _preset_diagonal_crack,
}


def _complete_state(d: dict[str, Any]) -> dict[str, Any]:
    """Fill in materials + any missing object lists so a state is serialisable."""
    d.setdefault("materials", default_library())
    for k in OBJECT_KEYS:
        d.setdefault(k, [])
    return d


def ensure_builtin_presets() -> None:
    """Seed each built-in preset file into presets/ if it is missing.

    Only *missing* files are written, so user edits are never overwritten and
    user-added presets coexist. Deleting a built-in brings it back next run; to
    retire one, edit its scene instead of deleting the file.
    """
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    for builder in _BUILTIN_BUILDERS.values():
        d = _complete_state(builder())
        target = PRESETS_DIR / f"{d.get('project_name', 'preset')}.gprstudio.json"
        if not target.exists():
            target.write_text(to_json(d), encoding="utf-8")


def _title_of(path: Path) -> str:
    """Human-readable name: the scene's title, else the file stem."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return str(data.get("title") or path.stem)
    except (OSError, json.JSONDecodeError):
        return path.stem


def _add_unique(out: dict[str, Path], title: str, path: Path) -> None:
    if title in out:  # disambiguate collisions by folder/file name
        title = f"{title} ({path.parent.name})"
    out[title] = path


def list_presets() -> dict[str, Path]:
    """Discover selectable scenes: curated presets + saved projects, by title.

    * curated presets — any ``presets/**/*.gprstudio.json`` (built-ins seeded here)
    * saved projects  — any ``projects/<folder>/*.gprstudio.json`` (new projects)
    """
    ensure_builtin_presets()
    out: dict[str, Path] = {}
    for path in sorted(PRESETS_DIR.rglob("*.gprstudio.json")):
        _add_unique(out, _title_of(path), path)
    if PROJECTS_DIR.exists():
        for path in sorted(PROJECTS_DIR.glob("*/*.gprstudio.json")):
            _add_unique(out, _title_of(path), path)
    return out


def load_preset(name: str) -> dict[str, Any]:
    """Load a named preset/project from its file into session-state updates."""
    path = list_presets().get(name)
    if path is None:
        raise ValueError(f"Preset '{name}' not found.")
    return _complete_state(from_json(path.read_text(encoding="utf-8")))


def _safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "scene"


def save_project(ss: Any) -> Path:
    """Save the current scene into projects/<name>/ so it is discoverable."""
    name = _safe_name(str(_get(ss, "project_name")))
    folder = PROJECTS_DIR / name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.gprstudio.json"
    path.write_text(to_json(ss), encoding="utf-8")
    return path
