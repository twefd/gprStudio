# CLAUDE.md — gprStudio

Project context for Claude Code / new contributors. This file travels with the
repo (unlike the machine-local `~/.claude` memory), so it's the portable source
of truth when picking the project up on another device.

## What this is

**gprStudio** is a local, guided **Streamlit** web app for modelling real
concrete-inspection **GPR** scenarios. You build a 2D cross-section (layers +
rebar + conduits + voids + cracks + oval channels), preview it, and run a B-scan
that approximates real gear (default: **Proceq GP8000**, its SFCW sweep modelled
as a Ricker pulse).

The heavy lifting — solving Maxwell's equations with the FDTD method — is done by
[**gprMax**](http://www.gprmax.com) (GPLv3), which is **vendored** under
`vendor/gprMax/` as a self-contained dependency. **This repo is the studio (our
work); gprMax is an unmodified* upstream dependency underneath it, not a fork.**
(* two packaging-only tweaks: a `pyproject.toml`, and a one-line `setup.py`
change so the unshipped 95 MB `tests/` dir isn't a required package.)

- GitHub: **`twefd/gprStudio`** (private).
- Local clone (this machine): `C:\Users\ZX\gprStudio`.
- Env: a conda env named **`gprMax`** (`%USERPROFILE%\miniconda3\envs\gprMax`),
  Python 3.13 on Windows. Git identity is repo-local: `twefd` /
  `zxzhong00@hotmail.com`.

## Repository layout

```
gprStudio/
  gpr_studio/            the Streamlit app (this is the project)
    app.py               4-step wizard UI (Equipment · Materials · Geometry · Run)
    model.py infile.py build.py preview.py runner.py scenes.py materials.py equipment.py
    presets/             curated .gprstudio.json scenes
    projects/            saved scenes (run outputs are git-ignored)
  vendor/gprMax/         the vendored gprMax FDTD solver (the dependency)
  run_gpr_studio.cmd/.ps1  Windows launchers
  requirements.txt       studio + gprMax deps (installs ./vendor/gprMax from source)
  packages.txt           apt build-essential (for Streamlit Cloud's Linux build)
  README.md              user-facing readme
  DEPLOY_STREAMLIT.md    how to deploy on Streamlit Community Cloud
```

## Branch model (IMPORTANT)

There are two products (GPU-enabled local; CPU-only online) and a production
branch the deploy server tracks. **Match the branch to the work:**

| Branch | Role | Deployed? |
|---|---|---|
| **`dev`** | the **local app** (GPU-enabled) — main priority. Do local/GPU work here. | no |
| **`online-dev`** | **online development** (CPU-only, GPU code stripped). Do online work here. | no |
| **`online`** | **production** — the branch Streamlit Community Cloud deploys. | **yes** |
| `master` | stale initial baseline — ignore. | — |

Rules:
- Never push WIP to `online`. Promote a *verified* online change with:
  `git checkout online && git merge online-dev && git push` — only that
  redeploys the live app.
- `dev` (GPU) and the online branches **diverge in `app.py`/`runner.py`** (GPU
  code present on `dev`, removed on online), so cherry-picking a *shared* bug-fix
  between them can conflict — resolve per-fix.
- Streamlit Cloud must be pointed at `online` (Manage app → Settings → Branch).

## Running locally

```powershell
# CPU (any branch):
& "$env:USERPROFILE\miniconda3\envs\gprMax\python.exe" -m streamlit run gpr_studio/app.py
# or the launcher (on `dev` it also sets up MSVC + CUDA for GPU runs):
.\run_gpr_studio.cmd
```

Headless self-test: `python -m gpr_studio.verify_studio` (build → preview →
geometry-only solve).

### Building the vendored gprMax extensions

The committed `.pyd` are Windows/py3.13 binaries. On a **new device / different
OS or Python**, rebuild them (needs a C compiler with OpenMP — MSVC Build Tools
on Windows, gcc on Linux):

```powershell
python vendor/gprMax/setup.py build      # builds *.pyd/*.so in place under vendor/gprMax/gprMax
```

### GPU acceleration (dev branch only)

Local GPU runs need: an NVIDIA GPU, the **CUDA Toolkit** (`nvcc` on PATH — this
machine has v13.3), **`pycuda`** in the env, and the **MSVC** host compiler so
`nvcc` can build the CUDA kernels. The `dev` launcher (`run_gpr_studio.cmd/.ps1`)
sets up the VS dev shell + CUDA automatically — **GPU only works when launched
that way** (or from a Developer Command Prompt with CUDA on PATH). The Run tab
shows a "Run on GPU (CUDA)" toggle when all prerequisites are present. On
`online*` branches this is all removed (CPU-only).

## Deploying (Streamlit Community Cloud)

Server tracks **`online`**, main file `gpr_studio/app.py`, **Python 3.12**. On
each push to `online` it auto-rebuilds: `packages.txt` installs `build-essential`,
then `pip install ./vendor/gprMax` compiles gprMax from source. It's **CPU-only**
(no GPU on the free tier), so keep models small. Full steps + limits:
`DEPLOY_STREAMLIT.md`.

## Architecture notes (the non-obvious bits)

- **The solver runs as a subprocess** (`python -m gprMax <in> [-n N] [--geometry-fixed] [-gpu id]`);
  gprMax is *never imported in-process*. `runner.GPRMAX_CWD` is the vendored tree
  when its extension is built there (local), otherwise the repo root — so on the
  Cloud the pip-installed (compiled) gprMax is used and the uncompiled vendored
  source can't shadow it. `app.py` keeps `vendor/gprMax` off `sys.path` entirely.
- **B-scan post-processing is self-contained h5py** (`runner._merge_out_files`,
  `_get_output_data`) — no `gprMax`/`tools` import — so merging works regardless
  of how gprMax is installed. (Byte-for-byte equal to gprMax's own merge.)
- **Run guard**: `section_run` sets `sim_running` in a button `on_click` before
  the body renders, so the run buttons render disabled and a second click is
  ignored (no concurrent/duplicate runs). `run_gprmax`/`run_bscan_parallel`
  terminate their subprocesses if a run is interrupted. After a run the outcome
  (status + log) is persisted to session_state and `st.rerun()` re-enables the
  buttons immediately.
- **Survey inputs (tab 1)** bind to their own keys seeded per `editor_nonce`, so
  they commit on the first interaction (the old `value=`+writeback pattern lagged
  a rerun). External updates (presets/import/equipment/fit) bump `editor_nonce`.
- **Geometry inputs are shown in mm** (converted at the data-editor boundary);
  the scene model and saved `.gprstudio.json` stay in cm.
- **CPU B-scan** uses a task farm (`run_bscan_parallel`, one process per worker,
  each `--geometry-fixed`). On the online branch the worker slider lives behind a
  "⚙️ Performance" popover. On `dev` there's also a GPU task farm (default 5
  workers, `gpu_worker_cap` by memory) and live GPU-load readout.

## Session history — 2026-08 (this chain of work)

Chronological summary of what was done, for continuity:

1. **Explored** the repo (gprMax core + the `gpr_studio` Streamlit layer);
   installed Streamlit; launched the app.
2. **Compiled** gprMax's Cython extensions with MSVC; verified an A-scan runs.
3. **Fixed tab-1 input lag** — Equipment/survey inputs now commit on the first
   press (keyed widgets instead of `value=`+writeback). Reproduced/verified in
   the real browser.
4. Standing rule adopted: **use a single `dev` branch** (later refined into the
   branch model above).
5. Added, then **reverted**, a click-to-place geometry-preview editor (user
   didn't want it).
6. **Merged** the `feat/gpr-concrete-studio` parallel B-scan task farm into dev;
   opened **PR #3** (installed `gh`).
7. **Rebranded** "GPR Concrete Studio"/`gprMax` UI → **gprStudio**; the GitHub
   repo was renamed to `twefd/gprStudio`.
8. **Made all geometry inputs display in mm** (cm/mm mix → mm), scene model
   unchanged.
9. **Split gprStudio into its own private repo** with gprMax **vendored** under
   `vendor/gprMax/` (this repo); wired the app to the vendored solver.
10. **Implemented GPU (CUDA) acceleration** — detection, `-gpu` wiring, Run-tab
    toggle. Installed CUDA Toolkit + built `pycuda`; launchers set up MSVC+CUDA.
    Verified a real GPU solve on the RTX 3060 Ti.
11. **Optimised GPU B-scans** — `--geometry-fixed` + a GPU task farm (naive 1
    process → ~4x faster; beats the CPU-8 farm). Default set to **5 GPU workers**.
    Benchmarked CPU vs GPU on coarse (~1.4x) and fine grids (~5.7x).
12. **Made the app deployable on Streamlit Cloud** — `requirements.txt` installs
    `./vendor/gprMax` from source, `packages.txt` (build-essential),
    `vendor/gprMax/pyproject.toml`, and an adaptive subprocess `cwd`.
13. **Debugged the cloud deploy** (a chain of `ModuleNotFoundError`): hardened
    the subprocess cwd, then made post-processing self-contained h5py, then took
    the vendored tree off `sys.path` — so the pip-built gprMax is never shadowed.
14. **Added the run guard** (no concurrent/duplicate simulations; subprocesses
    killed on interrupt).
15. **Set up the branch model**: `dev` (local GPU) vs `online` (deploy); then a
    dedicated **`online-dev`** WIP branch with **`online` as production** (so
    day-to-day pushes don't redeploy the live app).
16. **Slimmed `online` to CPU-only** (removed the GPU toggle/detection/params and
    the CUDA launcher setup on the online branches).
17. **Moved the worker slider** into a "⚙️ Performance" popover (online).
18. **Fixed the Run tab**: run buttons re-enable immediately after a run (persist
    outcome + `st.rerun()`); removed the "simulation is running" notice.

Commit messages carry the detail for each step.
