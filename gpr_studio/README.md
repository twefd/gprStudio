# gprStudio

A guided, local web application for modelling real concrete-inspection
scenarios with [gprMax](http://www.gprmax.com). Build a 2D cross-section
(layers + rebar + conduits + voids), preview it, and run a gprMax B-scan that
approximates real GPR gear — the **Proceq GP8000** by default.

## What it does

- **Visual geometry builder** — define top-down material layers and drop in
  rebar rows (cover depth & spacing), single bars/conduits/post-tension ducts,
  voids/delaminations, **diagonal cracks** (angled features), and **oval voids**
  (hollow-core channels / oval ducts). A live cross-section preview shows the
  model *before* you simulate.
- **Geometry presets** — one-click starting points: concrete slab in air,
  foundation beam with footing, delamination case, hollow-core slab
  (kanaalplaatvloer), and a diagonal-crack slab.
- **Save / import** — download the current scene as a `.gprstudio.json` file and
  re-import it later (round-trips geometry + survey + materials). You can also
  **paste from the clipboard** — either a scene JSON *or* a gprMax `.in` file,
  which is parsed back into editable layers / rebar / voids.
- **B-scan image viewer** — after a run, adjust component, colour map, gain, and
  axis units (X: trace / mm / cm / m, Y: ns / mm / cm / m) in-app, or open
  gprMax's own interactive viewer (pan / zoom / save) in a separate window. A
  full B-scan also saves a geometry PNG next to the outputs.
- **Material library** — common building materials with editable EM properties
  (dry/wet/fresh concrete, asphalt, dry/wet sand, gravel, clay, PVC, EPS foam,
  water, air voids, and steel → perfect conductor).
- **Equipment presets** — Proceq GP8000 (SFCW 0.2–4 GHz, modelled as an
  equivalent Ricker pulse) plus generic 1.5/2.0/2.6 GHz antennas. Cell size,
  time window and trace count are derived automatically.
- **One-click runs** — validate geometry, run a quick single A-scan, or run the
  full B-scan. The B-scan is merged and plotted automatically; per-trace files
  are cleaned up, keeping the merged data and the image.

## Requirements

- A working gprMax install (compiled Cython extensions) in a conda env.
- `streamlit` installed in that same env:
  ```powershell
  & "$env:USERPROFILE\miniconda3\envs\gprMax\python.exe" -m pip install streamlit
  ```

## Launch

From the repo root, use the batch launcher (double-click it, or):

```powershell
.\run_gpr_studio.cmd
```

or run Streamlit directly:

```powershell
& "$env:USERPROFILE\miniconda3\envs\gprMax\python.exe" -m streamlit run gpr_studio/app.py
```

> **Note:** `run_gpr_studio.ps1` may be blocked by PowerShell's script
> execution policy (`... cannot be loaded because running scripts is disabled`).
> The `.cmd` launcher is not affected by that policy. If you prefer the `.ps1`,
> run it with `powershell -ExecutionPolicy Bypass -File .\run_gpr_studio.ps1`.

Your browser opens the app. Walk the four tabs: **Equipment & survey →
Materials → Geometry & preview → Run & results**.

Generated input files, outputs and images are written to
`gpr_studio/projects/<project-name>/` (git-ignored).

## Modelling notes

- Models are **2D vertical cross-sections** — fast, and the way B-scans are
  read in the field.
- gprMax is a **time-domain (FDTD)** solver. The GP8000's stepped-frequency
  (SFCW) sweep is approximated by a broadband Ricker pulse at an adjustable
  centre frequency (default 2.0 GHz). This is a deliberate, labelled
  approximation.
- Material properties and the SFCW→Ricker mapping are approximate and
  site-dependent; all are adjustable in the UI so results stay physically
  honest.

## Module map

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI (the 4-step wizard) |
| `materials.py` | Material library + EM properties |
| `equipment.py` | GPR equipment presets (GP8000, generics) |
| `model.py` | Scene dataclasses (layers, rebar, bars, voids, diagonals, ovals, survey) |
| `infile.py` | Scene+Survey → gprMax `.in` (coordinate mapping, auto grid, triangles for angled/oval shapes) |
| `preview.py` | Matplotlib cross-section renderer |
| `runner.py` | Runs gprMax, merges + plots, adjustable B-scan viewer, cleans up |
| `build.py` | Shared state-dict → Scene/Survey conversion |
| `scenes.py` | Save/import scene files, gprMax `.in` parser, dynamic presets |

## Presets & saved projects

The **Preset / saved project** dropdown is populated dynamically from scene
files (`.gprstudio.json`) in two places:

- **Curated presets** — `gpr_studio/presets/*.gprstudio.json`. The five built-ins
  (slab in air, foundation beam, delamination, hollow-core slab, diagonal crack)
  are seeded here automatically if missing; edit or replace those files freely.
- **Saved projects** — `gpr_studio/projects/<folder>/*.gprstudio.json`. Build a
  scene and click **📁 Save to projects** (or **💾 Download scene** and place the
  file yourself); it then appears in the dropdown. This is the "new projects"
  path — separate from the curated presets.

Entries are named by each file's `title`; a title used in more than one place is
disambiguated with its folder name. Run artifacts (`.in/.out/.png/.vti`) under
`projects/` are git-ignored; the `.gprstudio.json` scene files are kept.
| `verify_studio.py` | Headless end-to-end self-test |
