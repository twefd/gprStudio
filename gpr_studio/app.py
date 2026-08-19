"""gprStudio — Streamlit application.

A four-step guided workflow on top of gprMax:
    1. Equipment & survey     2. Materials
    3. Geometry & preview     4. Run & results

Launch with:  <env python> -m streamlit run gpr_studio/app.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Only the repo root needs importing (for the ``gpr_studio`` package). gprMax is
# never imported in this process — it runs as a subprocess and post-processing is
# self-contained — so the vendored tree is deliberately kept OFF sys.path, which
# guarantees it can't shadow a pip-installed gprMax on a cloud deploy.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd
import streamlit as st

from gpr_studio.materials import Material, default_library
from gpr_studio.equipment import EQUIPMENT_PRESETS, DEFAULT_EQUIPMENT_KEY
from gpr_studio.model import (Scene, Survey, Layer, RebarRow, Bar, VoidBox,
                             DiagonalVoid, OvalRow)
from gpr_studio import infile, preview, runner, scenes, build

st.set_page_config(page_title="gprStudio", page_icon="📡",
                   layout="wide")

CM = 0.01      # cm -> m
MM = 0.001     # mm -> m
GHZ = 1.0e9


# --------------------------------------------------------------------------- #
# Session state defaults
# --------------------------------------------------------------------------- #
def _init_state() -> None:
    ss = st.session_state
    if "materials" in ss:
        return
    # Restore the user's persisted library; fall back to the built-in defaults.
    from gpr_studio.materials import load_library
    ss.materials = load_library() or default_library()
    ss.project_name = "slab_with_rebar"
    ss.title = "Concrete slab over sand with a rebar row"

    eq = EQUIPMENT_PRESETS[DEFAULT_EQUIPMENT_KEY]
    ss.equipment_key = eq.key
    ss.center_freq_ghz = eq.center_freq_hz / GHZ
    ss.tx_rx_offset_mm = eq.tx_rx_offset_m / MM
    ss.scan_length_cm = 60.0
    ss.trace_step_mm = eq.trace_step_m / MM
    ss.max_depth_cm = 40.0
    ss.pml_cells = 10
    ss.dx_override_mm = 0.0  # 0 => auto

    # A meaningful starting scene: 25 cm slab over sand, rebar row at 6 cm.
    ss.layers = [
        {"material": "dry_concrete", "thickness_cm": 25.0},
        {"material": "dry_sand", "thickness_cm": 20.0},
    ]
    ss.rebar = [
        {"label": "Top mat", "depth_cm": 6.0, "diameter_mm": 16.0,
         "spacing_cm": 15.0, "count": 4, "x_start_cm": 8.0, "material": "steel"},
    ]
    ss.bars = [
        {"label": "PVC conduit", "x_cm": 45.0, "depth_cm": 12.0,
         "diameter_mm": 50.0, "material": "pvc",
         "grout_material": "air", "grout_diameter_mm": 42.0},
    ]
    ss.voids = [
        {"label": "Air void", "x_cm": 25.0, "depth_cm": 18.0,
         "width_cm": 8.0, "height_cm": 3.0, "material": "air"},
    ]
    ss.diagonals = []
    ss.ovals = []
    ss.editor_nonce = 0  # bumped to force data_editors to reload on preset/import
    ss.last_run = None  # dict with paths after a run


def _apply_updates(updates: dict) -> None:
    """Apply a preset/imported scene dict to session state and refresh editors."""
    ss = st.session_state
    for k, v in updates.items():
        if k == "materials" and isinstance(v, dict):
            # Merge the scene's materials into the working library rather than
            # replacing it, so a preset/import never wipes the user's custom
            # materials or colours.
            merged = dict(ss.materials)
            merged.update(v)
            ss.materials = merged
            _persist_materials()
        else:
            ss[k] = v
    # Changing widget keys forces the data_editors to reload from the new state.
    ss.editor_nonce = ss.get("editor_nonce", 0) + 1


def _persist_materials() -> None:
    """Save the working library to disk if it changed since the last save.

    ``save_library`` itself refuses to write the pristine built-in defaults, so
    a default-state run can never clobber a user's customised file.
    """
    from gpr_studio.materials import (library_to_records, save_library,
                                      is_pristine_default, LIBRARY_FILE)
    import json as _json
    ss = st.session_state
    sig = _json.dumps(library_to_records(ss.materials), sort_keys=True)
    # Save when the library changed, OR when the file is missing but this session
    # still holds custom materials (self-heals a deleted / clobbered file).
    if ss.get("_materials_sig") != sig or (
            not LIBRARY_FILE.exists() and not is_pristine_default(ss.materials)):
        save_library(ss.materials)
        ss["_materials_sig"] = sig


def _do_import(text: str) -> None:
    """Import pasted/uploaded text (scene JSON or gprMax .in) into the app."""
    try:
        updates = scenes.import_text(text)
    except ValueError as exc:
        st.error(f"Import failed: {exc}")
        return
    note = updates.pop("_import_note", None)
    _apply_updates(updates)
    st.session_state["_import_note_pending"] = note or "Imported successfully."
    st.rerun()


# --------------------------------------------------------------------------- #
# Builders: session state -> domain objects
# --------------------------------------------------------------------------- #
def build_materials() -> dict[str, Material]:
    return st.session_state.materials


def material_names() -> list[str]:
    return list(st.session_state.materials.keys())


def build_scene() -> Scene:
    return build.scene_from_state(dict(st.session_state))


def build_survey(scene: Scene) -> Survey:
    return build.survey_from_state(dict(st.session_state), scene,
                                   build_materials())


# --------------------------------------------------------------------------- #
# Lag-free survey inputs
# --------------------------------------------------------------------------- #
# Streamlit lags one rerun behind when a widget takes its value in as a default
# and its return is written back (the old ``ss.x = st.number_input(value=ss.x)``
# pattern) — that is why tab 1 only updated on the *second* press. Instead bind
# each input to its own key so the widget owns its state (commits on the first
# interaction), seed that key once per ``editor_nonce`` from the canonical value,
# and mirror the live value straight back to ``ss[key]``. Anything that changes a
# survey value from outside the widget (presets, import, equipment change, fit
# scan) sets ``ss[key]`` and bumps ``editor_nonce`` so the widget re-seeds.
def _survey_number(label: str, key: str, *, cast=float, **kw):
    ss = st.session_state
    wkey = f"_inp_{key}_{ss.get('editor_nonce', 0)}"
    if wkey not in ss:
        ss[wkey] = cast(ss[key])
    ss[key] = st.number_input(label, key=wkey, **kw)
    return ss[key]


def _survey_slider(label: str, key: str, *, cast=float, **kw):
    ss = st.session_state
    wkey = f"_inp_{key}_{ss.get('editor_nonce', 0)}"
    if wkey not in ss:
        ss[wkey] = cast(ss[key])
    ss[key] = st.slider(label, key=wkey, **kw)
    return ss[key]


def _bump_nonce() -> None:
    """Force keyed survey inputs (and the editors) to re-seed from state."""
    st.session_state.editor_nonce = st.session_state.get("editor_nonce", 0) + 1


# --------------------------------------------------------------------------- #
# UI sections
# --------------------------------------------------------------------------- #
def section_equipment() -> None:
    ss = st.session_state
    st.subheader("1 · Equipment & survey")
    st.caption("Pick your GPR gear and how you scan. gprMax is time-domain, so "
               "an SFCW system like the GP8000 is modelled as an equivalent "
               "Ricker pulse — adjust the centre frequency to taste.")

    col1, col2 = st.columns(2)
    with col1:
        keys = list(EQUIPMENT_PRESETS.keys())
        idx = keys.index(ss.equipment_key) if ss.equipment_key in keys else 0
        chosen = st.selectbox(
            "GPR equipment preset", keys, index=idx,
            format_func=lambda k: EQUIPMENT_PRESETS[k].label)
        if chosen != ss.equipment_key:
            ss.equipment_key = chosen
            eq = EQUIPMENT_PRESETS[chosen]
            ss.center_freq_ghz = eq.center_freq_hz / GHZ
            ss.tx_rx_offset_mm = eq.tx_rx_offset_m / MM
            ss.trace_step_mm = eq.trace_step_m / MM
            _bump_nonce()  # re-seed the survey inputs from the new preset
            st.rerun()
        eq = EQUIPMENT_PRESETS[ss.equipment_key]
        st.info(eq.note)
        _survey_slider("Centre frequency [GHz]", "center_freq_ghz",
                       min_value=0.2, max_value=4.0, step=0.1)
        _survey_number("Tx–Rx antenna offset [mm]", "tx_rx_offset_mm",
                       min_value=0.0, max_value=300.0, step=5.0)

    with col2:
        _survey_number("Scan length [cm]", "scan_length_cm",
                       min_value=5.0, max_value=500.0, step=5.0)
        # Convenience: extend the scan so the antenna crosses every object.
        needed_cm = infile.full_scan_length(build_scene()) * 100
        if st.button(f"↔ Fit scan to model ({needed_cm:.0f} cm)",
                     help="Set scan length so the antenna passes over every "
                          "object in the geometry"):
            ss.scan_length_cm = round(needed_cm, 1)
            _bump_nonce()  # re-seed the scan-length input with the fitted value
            st.rerun()
        _survey_number("Trace spacing [mm]", "trace_step_mm",
                       min_value=1.0, max_value=50.0, step=1.0)
        _survey_number("Max depth of interest [cm]", "max_depth_cm",
                       min_value=5.0, max_value=150.0, step=5.0)
        with st.expander("Advanced (grid & boundary)"):
            _survey_number("Cell size override [mm] (0 = auto)", "dx_override_mm",
                           min_value=0.0, max_value=10.0, step=0.5)
            _survey_number("PML cells", "pml_cells", cast=int,
                           min_value=6, max_value=20, step=1)

    # Derived quantities
    scene = build_scene()
    survey = build_survey(scene)
    tw = infile.suggest_time_window(survey.max_depth_m, scene,
                                    build_materials(), survey.center_freq_hz)
    lay = infile.Layout(scene, survey, build_materials())
    nx = int(round(lay.width / survey.dx_m))
    ny = int(round(lay.height / survey.dx_m))

    st.markdown("**Derived model parameters**")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Cell size", f"{survey.dx_m * 1000:.2f} mm")
    m2.metric("Time window", f"{tw * 1e9:.1f} ns")
    m3.metric("Traces (B-scan)", f"{survey.num_traces}")
    m4.metric("Grid", f"{nx} × {ny}")
    st.caption(f"Domain ≈ {lay.width*100:.1f} × {lay.height*100:.1f} cm. "
               "Larger grids and more traces take longer to simulate.")

    # The antenna can only be moved in whole grid cells, so a trace step finer
    # than the cell size gets coarsened. Make that explicit.
    if survey.trace_step_eff_m > survey.trace_step_m + 1e-9:
        st.info(
            f"Trace step rounded to **{survey.trace_step_eff_m*1000:.2f} mm** "
            f"(one grid cell) — you requested {survey.trace_step_m*1000:.1f} mm, "
            f"but the antenna can't be moved less than one cell. To sample finer, "
            f"reduce the cell size (Advanced → cell size override) so the grid is "
            f"at least as fine as your trace step.")

    # Rough B-scan runtime estimate (very approximate; scales with cells×steps).
    est_s = nx * ny * (tw / 7.2e-12) * survey.num_traces / 4.0e10
    if survey.num_traces > 1 and est_s > 90:
        mins = est_s / 60.0
        st.warning(
            f"A full B-scan here is ~{survey.num_traces} traces and may take "
            f"roughly **{mins:.0f}–{mins*2:.0f} min**. Consider a larger trace "
            f"step, a shorter scan, or a coarser grid for a quick look.")


def _material_options() -> list[str]:
    return material_names()


def section_materials() -> None:
    ss = st.session_state
    st.subheader("2 · Materials")
    st.caption("Typical EM properties for building materials at GPR "
               "frequencies. Values are approximate and site-dependent — edit "
               "εr and conductivity to match your situation. 'Steel' maps to a "
               "perfect conductor (pec). Your edits are saved and restored "
               "automatically across sessions.")

    if st.button("↺ Reset to default materials",
                 help="Discard your saved library and restore the built-ins"):
        from gpr_studio.materials import delete_library
        ss.materials = default_library()
        delete_library()          # truly revert: remove the persisted file
        ss["_materials_sig"] = None
        ss.editor_nonce = ss.get("editor_nonce", 0) + 1
        st.rerun()

    n = ss.get("editor_nonce", 0)
    base_key = f"materials__base__{n}"
    if base_key not in ss:
        # Colour is edited via the swatches below, not in the table.
        ss[base_key] = pd.DataFrame([{
            "name": m.name, "label": m.label, "er": m.er, "sigma": m.sigma,
            "mu_r": m.mu_r, "sigma_star": m.sigma_star,
            "is_pec": m.is_pec, "note": m.note,
        } for m in ss.materials.values()])

    edited = st.data_editor(
        ss[base_key], hide_index=True, num_rows="dynamic", width="stretch",
        column_config={
            "name": st.column_config.TextColumn("id", help="gprMax identifier"),
            "label": st.column_config.TextColumn("Material"),
            "er": st.column_config.NumberColumn("εr", min_value=1.0, step=0.5),
            "sigma": st.column_config.NumberColumn("σ [S/m]", min_value=0.0,
                                                   step=0.001, format="%.3f"),
            "mu_r": st.column_config.NumberColumn("μr", min_value=1.0, step=0.1),
            "sigma_star": st.column_config.NumberColumn("σ*", min_value=0.0,
                                                        step=0.001, format="%.3f"),
            "is_pec": st.column_config.CheckboxColumn("Metal (pec)"),
            "note": st.column_config.TextColumn("Note", width="large"),
        },
        key=f"materials_editor_{n}",
    )

    # Rebuild the material dict from the edited table, preserving each colour.
    new_lib: dict[str, Material] = {}
    for _, r in edited.iterrows():
        name = str(r["name"]).strip()
        if not name or name == "nan":
            continue
        prev = ss.materials.get(name)
        new_lib[name] = Material(
            name=name, label=str(r["label"]), er=float(r["er"]),
            sigma=float(r["sigma"]), mu_r=float(r["mu_r"]),
            sigma_star=float(r["sigma_star"]),
            color=prev.color if prev else "#cccccc",
            note=str(r["note"]), is_pec=bool(r["is_pec"]))
    if new_lib:
        ss.materials = new_lib

    # Colour swatches — live picker per material; the geometry preview and the
    # legend update immediately when you change one.
    st.markdown("**Colours** — click a swatch to change (preview updates live)")
    mats = list(ss.materials.values())
    ncols = 4
    cols = st.columns(ncols)
    for i, m in enumerate(mats):
        with cols[i % ncols]:
            picked = st.color_picker(m.label, value=m.color,
                                     key=f"colpick_{m.name}_{n}")
            ss.materials[m.name].color = picked

    # Persist the library so deletions / colour / property edits survive refresh.
    _persist_materials()


def _mat_col(label: str = "Material"):
    return st.column_config.SelectboxColumn(label, options=_material_options(),
                                            required=True, width="medium")


# Default row seeded when a geometry element is first added.
_ELEMENT_TEMPLATES: dict[str, dict] = {
    "layers": {"material": "dry_concrete", "thickness_cm": 20.0},
    "rebar": {"label": "Rebar", "depth_cm": 6.0, "diameter_mm": 16.0,
              "spacing_cm": 15.0, "count": 4, "x_start_cm": 10.0,
              "material": "steel"},
    "voids": {"label": "Block", "x_cm": 20.0, "depth_cm": 10.0,
              "width_cm": 10.0, "height_cm": 4.0, "material": "air"},
    "bars": {"label": "Conduit", "x_cm": 30.0, "depth_cm": 12.0,
             "diameter_mm": 50.0, "material": "pvc", "grout_material": "",
             "grout_diameter_mm": 0.0},
    "diagonals": {"label": "Crack", "x_cm": 30.0, "depth_cm": 5.0,
                  "length_cm": 15.0, "angle_deg": 45.0, "aperture_mm": 5.0,
                  "material": "air"},
    "ovals": {"label": "Oval", "cx_cm": 20.0, "depth_cm": 13.0,
              "width_cm": 12.0, "height_cm": 15.0, "count": 1,
              "spacing_cm": 15.0, "material": "air"},
}


def _element_colconfig(key: str) -> dict:
    # Short headers + fixed small widths so the grids fit the column without
    # horizontal scrolling. Full descriptions live in each column's tooltip.
    C = st.column_config

    def num(label, help, **kw):
        return C.NumberColumn(label, help=help, width="small", **kw)

    def txt(label, help="", w="small"):
        return C.TextColumn(label, help=help, width=w)

    # All lengths are shown/entered in millimetres. The ``*_cm`` state fields are
    # scaled by _element_editor at the editor boundary, so these columns keep
    # their ``*_cm`` keys but display mm values (min/step are the mm equivalents).
    if key == "layers":
        return {"material": _mat_col(),
                "thickness_cm": num("Thick. mm", "Layer thickness [mm]",
                                    min_value=5.0, step=5.0)}
    if key == "rebar":
        return {"label": txt("Label"),
                "depth_cm": num("Cover mm", "Cover depth to bar centre [mm]",
                                min_value=0.0, step=5.0),
                "diameter_mm": num("Ø mm", "Bar diameter [mm]", min_value=1.0, step=1.0),
                "spacing_cm": num("Spc mm", "Centre-to-centre spacing [mm]",
                                  min_value=10.0, step=10.0),
                "count": num("n", "Number of bars", min_value=1, step=1),
                "x_start_cm": num("x₀ mm", "First bar x-position [mm]",
                                  min_value=0.0, step=10.0),
                "material": _mat_col()}
    if key == "voids":
        return {"label": txt("Label"),
                "x_cm": num("x mm", "Left edge x [mm]", min_value=0.0, step=10.0),
                "depth_cm": num("Top mm", "Depth to the top [mm]", min_value=0.0, step=5.0),
                "width_cm": num("W mm", "Width [mm]", min_value=5.0, step=5.0),
                "height_cm": num("H mm", "Height [mm]", min_value=2.0, step=2.0),
                "material": _mat_col()}
    if key == "bars":
        return {"label": txt("Label"),
                "x_cm": num("x mm", "Centre x [mm]", min_value=0.0, step=10.0),
                "depth_cm": num("Depth mm", "Depth to centre [mm]", min_value=0.0, step=5.0),
                "diameter_mm": num("Ø mm", "Diameter [mm]", min_value=1.0, step=1.0),
                "material": _mat_col(),
                "grout_material": C.SelectboxColumn(
                    "Core", help="Optional filled core (e.g. grout)",
                    width="small", options=[""] + _material_options()),
                "grout_diameter_mm": num("Core Ø", "Core diameter [mm]",
                                         min_value=0.0, step=1.0)}
    if key == "diagonals":
        return {"label": txt("Label"),
                "x_cm": num("x mm", "Start x [mm]", min_value=0.0, step=10.0),
                "depth_cm": num("Depth mm", "Start depth [mm]", min_value=0.0, step=5.0),
                "length_cm": num("Len mm", "Length [mm]", min_value=5.0, step=5.0),
                "angle_deg": num("Ang °", "Angle from horizontal; + tilts "
                                 "down-right [°]", min_value=-89.0, max_value=89.0,
                                 step=5.0),
                "aperture_mm": num("Ap mm", "Aperture / crack width [mm]",
                                   min_value=0.5, step=0.5),
                "material": _mat_col()}
    if key == "ovals":
        return {"label": txt("Label"),
                "cx_cm": num("x mm", "First centre x [mm]", min_value=0.0, step=10.0),
                "depth_cm": num("Depth mm", "Centre depth [mm]", min_value=0.0, step=5.0),
                "width_cm": num("W Ø mm", "Horizontal diameter [mm]",
                                min_value=5.0, step=5.0),
                "height_cm": num("H Ø mm", "Vertical diameter [mm]",
                                 min_value=5.0, step=5.0),
                "count": num("n", "Number of ovals", min_value=1, step=1),
                "spacing_cm": num("Spc mm", "Centre-to-centre spacing [mm]",
                                  min_value=0.0, step=10.0),
                "material": _mat_col()}
    return {}


def _seed_element(state_key: str) -> None:
    """Callback: add an optional element with a sensible default row."""
    ss = st.session_state
    ss[state_key] = [dict(_ELEMENT_TEMPLATES[state_key])]
    ss.editor_nonce = ss.get("editor_nonce", 0) + 1


def _remove_element(state_key: str) -> None:
    """Callback: clear an optional element back to its 'add' button."""
    ss = st.session_state
    ss[state_key] = []
    ss.editor_nonce = ss.get("editor_nonce", 0) + 1


def _element_editor(state_key: str, title: str, optional: bool = False) -> None:
    """Render one geometry element as a data editor.

    Uses a stable base DataFrame (created once per ``editor_nonce``) as the
    editor input so edits commit on the first Enter — passing a freshly rebuilt
    DataFrame every rerun is what caused the 'press Enter twice' behaviour.
    Optional elements collapse to a single '➕ Add' button when empty.
    """
    ss = st.session_state
    n = ss.get("editor_nonce", 0)
    rows = ss.get(state_key) or []

    if optional and not rows:
        st.button(f"➕ Add {title}", key=f"add_{state_key}_{n}",
                  on_click=_seed_element, args=(state_key,))
        return

    template = _ELEMENT_TEMPLATES[state_key]
    # The tables show every length in millimetres for consistency, while the
    # scene state (and saved .gprstudio.json) keep these ``*_cm`` fields in
    # centimetres. Convert cm -> mm when seeding the editor and mm -> cm on read.
    cm_cols = [k for k in template if k.endswith("_cm")]
    base_key = f"{state_key}__base__{n}"
    if base_key not in ss:
        df = pd.DataFrame(rows, columns=list(template.keys()))
        for c in cm_cols:
            df[c] = pd.to_numeric(df[c], errors="coerce") * 10.0
        ss[base_key] = df

    hdr = st.columns([5, 1])
    hdr[0].markdown(f"**{title}**")
    if optional:
        hdr[1].button("✕", key=f"rm_{state_key}_{n}", help=f"Remove all {title}",
                      on_click=_remove_element, args=(state_key,))

    edited = st.data_editor(
        ss[base_key], hide_index=True, num_rows="dynamic", width="stretch",
        row_height=40, key=f"{state_key}_editor_{n}",
        column_config=_element_colconfig(state_key))
    records = edited.to_dict("records")
    for r in records:
        for c in cm_cols:
            v = r.get(c)
            if v is not None and v == v:  # skip None / NaN (blank cells)
                r[c] = float(v) / 10.0
    ss[state_key] = records


def section_geometry() -> None:
    ss = st.session_state
    n = ss.get("editor_nonce", 0)
    st.subheader("3 · Geometry & preview")
    st.caption("Build the cross-section from top-down layers plus embedded "
               "objects. The preview updates live — nothing is simulated yet.")

    # Keep the right-hand preview column pinned while scrolling the editors.
    # The column (a flex item) stretches tall next to the editors, so making the
    # column itself sticky gives it room to stay in view; :has() scopes it to
    # only the column that holds the preview marker. top:3.9rem clears the ~60px
    # Streamlit header so the pinned heading is fully visible.
    st.markdown(
        "<style>[data-testid=\"stColumn\"]:has(.st-key-geom_preview_sticky){"
        "align-self:flex-start;position:sticky;top:3.9rem;z-index:1;}</style>",
        unsafe_allow_html=True)

    # --- Preset / save / import: selectbox (50%) then Load · Save · Import ----
    c_sel, c_load, c_save, c_imp = st.columns(
        [3, 1, 1, 1.1], vertical_alignment="bottom")
    with c_sel:
        preset_names = list(scenes.list_presets())
        preset_name = st.selectbox("Preset / saved project", preset_names,
                                   index=None, placeholder="Choose a scene…",
                                   key=f"preset_sel_{n}",
                                   help="Curated presets live in gpr_studio/"
                                        "presets/; saved projects in "
                                        "gpr_studio/projects/<folder>/")
    with c_load:
        if st.button("Load", disabled=preset_name is None, width="stretch",
                     help="Replace the current geometry & survey with the "
                          "selected scene"):
            _apply_updates(scenes.load_preset(preset_name))
            st.rerun()
    with c_save:
        if st.button("Save", width="stretch",
                     help="Save into gpr_studio/projects/ so it appears in the "
                          "list at left"):
            path = scenes.save_project(ss)
            ss["_import_note_pending"] = (
                f"Saved to {path.relative_to(scenes._ROOT.parent)}")
            st.rerun()
    with c_imp:
        with st.popover("📥 Import", width="stretch"):
            up = st.file_uploader("Upload a .gprstudio.json scene or a gprMax .in",
                                  type=["json", "in", "txt"], key=f"import_{n}")
            if up is not None:
                _do_import(up.getvalue().decode("utf-8", errors="replace"))
            st.caption("…or paste from the clipboard:")
            pasted = st.text_area(
                "Paste a gprMax .in file or a .gprstudio.json scene",
                height=140, key=f"paste_{n}", label_visibility="collapsed",
                placeholder="#title: ...\n#domain: ...\n#dx_dy_dz: ...")
            if st.button("Import pasted text", disabled=not pasted.strip()):
                _do_import(pasted)

    note = ss.pop("_import_note_pending", None)
    if note:
        st.info(note)

    st.divider()

    left, right = st.columns([1, 1])
    with left:
        ss.title = st.text_input("Scene title", ss.title)
        ss.project_name = st.text_input("Project name (folder)", ss.project_name)

        # Standard elements — always shown.
        _element_editor("layers", "Layers (top = surface; last is half-space)")
        _element_editor("rebar", "Rebar rows (cover depth & spacing)")
        _element_editor("voids",
                        "Solid blocks (voids / delamination / honeycomb)")

        # Optional elements — collapse to an 'add' button until needed.
        st.caption("Optional elements — add only what you need:")
        _element_editor("bars", "Bars / conduits / ducts", optional=True)
        _element_editor("diagonals", "Diagonal cracks / voids", optional=True)
        _element_editor("ovals", "Oval voids (hollow-core channels)",
                        optional=True)

    with right:
        # Sticky container: preview + generated .in stay visible while scrolling.
        with st.container(key="geom_preview_sticky"):
            st.markdown("**Cross-section preview**")
            try:
                scene = build_scene()
                survey = build_survey(scene)
                reach_cm = infile.full_scan_length(scene, margin_m=0.0) * 100
                if survey.scan_length_m * 100 + 1e-6 < reach_cm:
                    st.warning(
                        f"Scan length ({survey.scan_length_m*100:.0f} cm) does "
                        f"not cover the whole model (objects reach "
                        f"{reach_cm:.0f} cm). Use **Fit scan to model** on the "
                        "Equipment tab.")
                fig = preview.render(scene, survey, build_materials())
                st.pyplot(fig, width="stretch")

                with st.expander("Generated gprMax input file (.in)"):
                    text = infile.generate(scene, survey, build_materials())
                    st.code(text, language="text")
                    st.download_button("Download .in", text,
                                       file_name=f"{ss.project_name}.in")
            except Exception as exc:  # noqa: BLE001 - surface errors to the user
                st.error(f"Preview / input-file error: {exc}")


def _request_run(kind: str) -> None:
    """Button callback: queue a run, unless one is already in progress.

    Runs before the script body, so the buttons that render this cycle come out
    disabled (``sim_running`` is already True) and a second click lands on a
    disabled button — Streamlit ignores it, so no duplicate/concurrent run.
    """
    ss = st.session_state
    if ss.get("sim_running"):
        return
    ss["sim_running"] = True
    ss["_run_request"] = kind


def section_run() -> None:
    ss = st.session_state
    st.subheader("4 · Run & results")
    scene = build_scene()
    survey = build_survey(scene)
    st.caption(f"Ready to simulate **{scene.title}** — "
               f"{survey.num_traces} traces, {survey.dx_m*1000:.2f} mm cells.")

    # --- Compute backend: CPU (OpenMP task farm) or GPU (CUDA) ---
    gpu = _gpu_status_cached()
    use_gpu = False
    gpu_device = 0
    gc1, gc2 = st.columns([4, 1], vertical_alignment="bottom")
    with gc1:
        if gpu["cards"]:
            st.caption("🖥️ GPU: " + " · ".join(
                f"[{i}] {name} ({mem} MiB)" for i, name, mem in gpu["cards"]))
        else:
            st.caption("🖥️ No CUDA GPU detected — running on CPU.")
    with gc2:
        if st.button("↻ Re-check", width="stretch",
                     help="Re-detect CUDA availability (after installing "
                          "pycuda / CUDA Toolkit)"):
            _gpu_status_cached.clear()
            st.rerun()

    if gpu["ready"]:
        use_gpu = st.toggle(
            "⚡ Run on GPU (CUDA)", key="use_gpu",
            help="Run the gprMax FDTD solver on the NVIDIA GPU instead of the "
                 "CPU. Big speed-up on larger grids; identical results.")
        if use_gpu and len(gpu["cards"]) > 1:
            names = {i: n for i, n, _ in gpu["cards"]}
            gpu_device = st.selectbox(
                "GPU device", [c[0] for c in gpu["cards"]],
                format_func=lambda i: f"{i}: {names[i]}", key="gpu_device")
    elif gpu["cards"]:
        st.info(gpu["message"] + "  \nSee **README → GPU acceleration** to set "
                "it up, then click **Re-check**.")

    # Worker count. On GPU a few concurrent processes overlap per-trace CPU
    # setup and multiplex the card (one process leaves it ~30% busy); on CPU
    # the task farm splits traces across cores.
    cores = os.cpu_count() or 4
    if use_gpu:
        gpu_mem = next((int(m) for i, _, m in gpu["cards"]
                        if i == gpu_device and str(m).isdigit()), None)
        gpu_cap = runner.gpu_worker_cap(gpu_mem)
        default_gpu = runner.auto_gpu_workers(survey.num_traces, gpu_mem)
        workers = st.slider(
            "⚡ GPU workers (B-scan)", 1, max(gpu_cap, 1), default_gpu,
            key="gpu_workers",
            help="Concurrent gprMax processes sharing the GPU (default 5). One "
                 "process alone underuses the card; ~5 is the sweet spot on small "
                 "2D models, and on large/fine grids the GPU is already saturated "
                 "so fewer would do. Capped by GPU memory (~1.3 GiB/worker). "
                 "Identical results.")
        threads_per = max(1, cores // max(workers, 1))
        st.caption(f"{workers} GPU worker(s) on device **{gpu_device}** — each "
                   "uses `--geometry-fixed` (geometry built once, only the "
                   "antenna moves).")
    else:
        default_workers = runner.auto_workers(survey.num_traces)
        workers = st.slider(
            "⚡ Parallel workers (B-scan)", 1, cores, default_workers,
            key="bscan_workers",
            help="Split the B-scan traces across this many gprMax processes. "
                 "~8 is the sweet spot (beyond that these small models saturate "
                 "memory bandwidth). Results are identical to a sequential run. "
                 "Set to 1 to keep the machine responsive.")
        threads_per = max(1, cores // workers)
        st.caption(f"{workers} worker(s) × {threads_per} thread(s) — uses "
                   f"`--geometry-fixed` (geometry built once, only the antenna moves).")

    running = ss.get("sim_running", False)
    c1, c2, c3 = st.columns(3)
    c1.button("Validate geometry", width="stretch", disabled=running,
              on_click=_request_run, args=("geom",),
              help="Fast --geometry-only build to check the model parses")
    c2.button("Run single A-scan", width="stretch", disabled=running,
              on_click=_request_run, args=("ascan",),
              help="One trace: quick sanity check")
    c3.button("Run full B-scan", type="primary", width="stretch",
              disabled=running, on_click=_request_run, args=("bscan",),
              help="Full survey; produces the B-scan image")
    if running:
        st.info("⏳ A simulation is running — the run buttons are disabled until "
                "it finishes.")

    kind = ss.pop("_run_request", None)
    if kind is None:
        ss["sim_running"] = False   # idle — keep the buttons enabled
        _show_last_result()
        return
    do_geom, do_ascan, do_bscan = kind == "geom", kind == "ascan", kind == "bscan"

    text = infile.generate(scene, survey, build_materials())
    in_path = runner.write_infile(text, ss.project_name)
    base = in_path.with_suffix("")  # projects/x/x

    log_box = st.expander("Solver log", expanded=False)
    log_lines: list[str] = []
    progress = st.progress(0.0, text="Starting gprMax…")
    status = st.empty()

    def on_line(line: str) -> None:
        log_lines.append(line)
        pr = runner.parse_progress(line)
        if pr:
            cur, tot = pr
            progress.progress(cur / max(tot, 1),
                              text=f"Simulating model {cur}/{tot}")

    if do_bscan:
        n = survey.num_traces
        gpu_arg = gpu_device if use_gpu else None
        unit = "GPU worker" if use_gpu else "worker"

        gpu_peak = {"util": 0}  # track peak GPU load across the run

        def on_prog(done: int, tot: int) -> None:
            if use_gpu:
                g = runner.gpu_utilization(gpu_device)
                if g:
                    gpu_peak["util"] = max(gpu_peak["util"], g["util"])
                    load = (f" · GPU {g['util']}% · "
                            f"{g['mem_used']/1024:.1f}/{g['mem_total']/1024:.1f} GB")
                else:
                    load = ""
                where = f"GPU {gpu_device} · {workers} workers{load}"
            else:
                where = f"{workers}×{threads_per} threads"
            progress.progress(done / max(tot, 1),
                              text=f"Simulating trace {done}/{tot} · {where}")

        with st.spinner(f"Running B-scan on {workers} {unit}(s)…"):
            rc, plog = runner.run_bscan_parallel(in_path, n, workers,
                                                 on_progress=on_prog,
                                                 gpu=gpu_arg)
        log_lines = plog.splitlines()
        if use_gpu and gpu_peak["util"]:
            st.caption(f"⚡ Peak GPU load during the run: {gpu_peak['util']}% "
                       "— raise **GPU workers** if it stayed low on a small model.")
    else:
        # A-scan honours the GPU toggle; geometry-only never needs the solver.
        gpu_arg = gpu_device if (use_gpu and do_ascan) else None
        where = f" on GPU device {gpu_device}" if gpu_arg is not None else ""
        with st.spinner(f"Running gprMax{where}…"):
            rc = runner.run_gprmax(in_path, geometry_only=do_geom,
                                   on_line=on_line, gpu=gpu_arg)
    # The run finished (an interrupted run raises before here and leaves the flag
    # set — cleared on the next idle rerun — with its subprocesses terminated).
    ss["sim_running"] = False
    progress.empty()
    log_box.code("\n".join(log_lines[-80:]) or "(no output)", language="text")

    if rc != 0:
        status.error(f"gprMax exited with code {rc}. See the solver log.")
        return

    if do_geom:
        status.success("Geometry built successfully — the model is valid.")
        ss.last_run = None
    elif do_ascan:
        png = runner.plot_ascan(base.with_suffix(".out"))
        status.success("A-scan complete.")
        ss.last_run = {"kind": "ascan", "png": str(png)}
    else:
        merged, png = runner.make_bscan(base)
        # Also export a PNG of the geometry alongside the B-scan output.
        geom_png = base.parent / f"{base.name}_geometry.png"
        try:
            gfig = preview.render(scene, survey, build_materials())
            gfig.savefig(geom_png, dpi=130, bbox_inches="tight")
            import matplotlib.pyplot as _plt
            _plt.close(gfig)
        except Exception:  # noqa: BLE001 - geometry image is a nice-to-have
            geom_png = None
        # Conversion data for the B-scan axis units.
        mats = build_materials()
        top = mats.get(scene.layers[0].material) if scene.layers else None
        vel = top.velocity() if top else 1.0e8
        status.success("B-scan complete. Per-trace files cleaned up; merged "
                       "file, B-scan image and geometry image kept.")
        ss.last_run = {"kind": "bscan", "png": str(png), "merged": str(merged),
                       "geometry_png": str(geom_png) if geom_png else None,
                       "trace_step_m": survey.trace_step_eff_m,
                       "velocity_m_s": vel}
    _show_last_result()


@st.cache_data(ttl=30, show_spinner="Checking GPU…")
def _gpu_status_cached() -> dict:
    """Cache GPU detection briefly (the probes shell out); a button clears it."""
    return runner.gpu_status()


@st.cache_data(show_spinner=False)
def _cached_bscan_png(merged: str, component: str, cmap: str, gain: float,
                      x_unit: str, y_unit: str, trace_step_m: float,
                      velocity_m_s: float, _mtime: float) -> str:
    """Render (and cache) a B-scan PNG; re-renders only when inputs change."""
    return str(runner.render_bscan(
        Path(merged), component, cmap, gain, x_unit=x_unit, y_unit=y_unit,
        trace_step_m=trace_step_m or None, velocity_m_s=velocity_m_s or None))


def _show_last_result() -> None:
    last = st.session_state.get("last_run")
    if not last:
        return
    st.divider()
    if last["kind"] == "bscan":
        st.markdown("**B-scan result — image viewer**")
        merged = last["merged"]
        comps = runner.available_components(Path(merged))
        default_comp = comps.index("Ez") if "Ez" in comps else 0

        c1, c2, c3 = st.columns(3)
        comp = c1.selectbox("Component", comps, index=default_comp,
                            key="bscan_comp")
        cmap = c2.selectbox("Colour map",
                            ["gray", "seismic", "RdBu", "bwr", "viridis",
                             "magma", "hot"], key="bscan_cmap")
        gain = c3.slider("Gain / contrast", 0.2, 10.0, 1.0, 0.1,
                         key="bscan_gain",
                         help="Higher gain brings up weak reflections")

        a1, a2 = st.columns(2)
        x_unit = a1.selectbox("X-axis", ["trace", "mm", "cm", "m"],
                              key="bscan_xunit",
                              help="Trace number, or distance using the trace "
                                   "spacing")
        y_unit = a2.selectbox("Y-axis", ["ns", "mm", "cm", "m"],
                              key="bscan_yunit",
                              help="Two-way time, or depth using the top-layer "
                                   "velocity (depth = v·t/2, approximate)")
        trace_step = float(last.get("trace_step_m") or 0.0)
        velocity = float(last.get("velocity_m_s") or 0.0)
        if y_unit != "ns" and velocity:
            a2.caption(f"depth uses v≈{velocity/1e8:.2f}×10⁸ m/s "
                       f"(εr≈{(3e8/velocity)**2:.1f})")

        try:
            mtime = os.path.getmtime(merged)
            png = _cached_bscan_png(merged, comp, cmap, float(gain), x_unit,
                                    y_unit, trace_step, velocity, mtime)
            st.image(png, width="stretch")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not render B-scan: {exc}")
            png = last.get("png")

        b1, b2 = st.columns(2)
        if b1.button("🔍 Open in interactive gprMax viewer",
                     help="Opens gprMax's own B-scan window (pan / zoom / save) "
                          "on your desktop"):
            try:
                runner.open_bscan_viewer(Path(merged), comp)
                b1.success("Opening gprMax viewer in a new window…")
                b1.caption("No window? The app must be launched from your own "
                           "terminal (run_gpr_studio.cmd), not a background "
                           "process, for desktop windows to show.")
            except Exception as exc:  # noqa: BLE001
                b1.error(f"Could not open viewer: {exc}")
        if png:
            with open(png, "rb") as fh:
                b2.download_button("Download PNG", fh.read(),
                                   file_name=Path(png).name)
        st.caption(f"Merged data: `{merged}`")
        geom = last.get("geometry_png")
        if geom and os.path.exists(geom):
            with st.expander("Geometry image (saved with the output)"):
                st.image(geom, width="stretch")
                st.caption(f"Saved to `{geom}`")
    elif last["kind"] == "ascan":
        st.markdown("**A-scan result**")
        st.image(last["png"], width="stretch")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    _init_state()
    st.title("gprStudio")
    st.caption("GPR model simulation using gprMax")

    tabs = st.tabs(["① Equipment & survey", "② Materials",
                    "③ Geometry & preview", "④ Run & results"])
    with tabs[0]:
        section_equipment()
    with tabs[1]:
        section_materials()
    with tabs[2]:
        section_geometry()
    with tabs[3]:
        section_run()


main()
