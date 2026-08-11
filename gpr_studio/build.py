"""Convert the app's editable state (UI-unit dicts) into domain objects.

Kept free of Streamlit so the app, the scene importer and headless tests can all
share one conversion. ``state`` is a mapping with the same keys the app keeps in
``st.session_state`` (lengths in cm, diameters/apertures in mm, freq in GHz).
"""

from __future__ import annotations

from typing import Any

from .model import (Scene, Survey, Layer, RebarRow, Bar, VoidBox,
                    DiagonalVoid, OvalRow)
from . import infile

CM = 0.01
MM = 0.001
GHZ = 1.0e9


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def scene_from_state(s: dict) -> Scene:
    layers = [Layer(r["material"], _f(r["thickness_cm"]) * CM)
              for r in s.get("layers", []) if r.get("material")]
    rebar = [RebarRow(
        depth_m=_f(r["depth_cm"]) * CM, diameter_m=_f(r["diameter_mm"]) * MM,
        spacing_m=_f(r["spacing_cm"]) * CM, count=int(_f(r["count"])),
        x_start_m=_f(r["x_start_cm"]) * CM, material=r.get("material", "steel"),
        label=r.get("label", "Rebar row"),
    ) for r in s.get("rebar", []) if int(_f(r.get("count", 0))) > 0]
    bars = [Bar(
        x_m=_f(b["x_cm"]) * CM, depth_m=_f(b["depth_cm"]) * CM,
        diameter_m=_f(b["diameter_mm"]) * MM, material=b.get("material", "steel"),
        grout_material=(b.get("grout_material") or None),
        grout_diameter_m=_f(b.get("grout_diameter_mm", 0.0)) * MM,
        label=b.get("label", "Bar"),
    ) for b in s.get("bars", []) if b.get("material")]
    voids = [VoidBox(
        x_m=_f(v["x_cm"]) * CM, depth_m=_f(v["depth_cm"]) * CM,
        width_m=_f(v["width_cm"]) * CM, height_m=_f(v["height_cm"]) * CM,
        material=v.get("material", "air"), label=v.get("label", "Void"),
    ) for v in s.get("voids", []) if v.get("material")]
    diagonals = [DiagonalVoid(
        x_m=_f(d["x_cm"]) * CM, depth_m=_f(d["depth_cm"]) * CM,
        length_m=_f(d["length_cm"]) * CM, angle_deg=_f(d["angle_deg"]),
        aperture_m=_f(d["aperture_mm"]) * MM, material=d.get("material", "air"),
        label=d.get("label", "Diagonal crack"),
    ) for d in s.get("diagonals", []) if d.get("material")]
    ovals = [OvalRow(
        cx_start_m=_f(o["cx_cm"]) * CM, depth_m=_f(o["depth_cm"]) * CM,
        width_m=_f(o["width_cm"]) * CM, height_m=_f(o["height_cm"]) * CM,
        count=int(_f(o.get("count", 1))), spacing_m=_f(o.get("spacing_cm", 0.0)) * CM,
        material=o.get("material", "air"), label=o.get("label", "Oval void"),
    ) for o in s.get("ovals", []) if o.get("material") and int(_f(o.get("count", 0))) > 0]
    return Scene(name=s.get("project_name", "scene"),
                 title=s.get("title", "gprStudio model"),
                 layers=layers, rebar_rows=rebar, bars=bars, voids=voids,
                 diagonals=diagonals, ovals=ovals)


def survey_from_state(s: dict, scene: Scene, materials: dict) -> Survey:
    center = _f(s.get("center_freq_ghz", 2.0)) * GHZ
    override = _f(s.get("dx_override_mm", 0.0)) * MM
    dx = override if override > 0 else infile.suggest_dx(center, scene, materials)
    return Survey(
        equipment_key=s.get("equipment_key", "gp8000"), center_freq_hz=center,
        tx_rx_offset_m=_f(s.get("tx_rx_offset_mm", 40.0)) * MM,
        scan_length_m=_f(s.get("scan_length_cm", 60.0)) * CM,
        trace_step_m=_f(s.get("trace_step_mm", 8.0)) * MM,
        max_depth_m=_f(s.get("max_depth_cm", 40.0)) * CM,
        dx_m=dx, pml_cells=int(_f(s.get("pml_cells", 10))))
