# gprStudio

A guided, local web app for modelling real concrete-inspection GPR scenarios.
Build a 2D cross-section (layers + rebar + conduits + voids), preview it, and run
B-scans

gprStudio is a [Streamlit](https://streamlit.io) front-end. The underlying simulation is done by
[**gprMax**](http://www.gprmax.com), which is **vendored** into this repository
under [`vendor/gprMax/`](vendor/gprMax/) as a self-contained dependency. 

## Repository layout

```
gprStudio/
  gpr_studio/          the Streamlit app (this is the project)
    app.py             4-step wizard UI
    model.py infile.py build.py preview.py runner.py scenes.py …
    presets/           curated .gprstudio.json scenes
    projects/          your saved scenes (run outputs are git-ignored)
  vendor/
    gprMax/            the gprMax FDTD solver — the dependency
  run_gpr_studio.cmd   Windows launcher
  requirements.txt     studio + gprMax runtime deps
```

The studio finds the solver by putting `vendor/gprMax` on `sys.path` (so
`import gprMax` / `tools` resolve) and by running `python -m gprMax` with that
directory as its working directory.

## Setup

1. **Create the environment** (Miniconda recommended) and install dependencies:
   ```powershell
   conda create -n gprMax python=3.11 -y
   conda activate gprMax
   pip install -r requirements.txt
   ```
2. **Build gprMax's Cython extensions** — needed unless the committed compiled
   extensions already match your platform/Python. From the repo root, in a
   Developer Command Prompt (MSVC on Windows / gcc on Linux/macOS):
   ```powershell
   python vendor/gprMax/setup.py build
   ```
   This builds the extensions in place under `vendor/gprMax/gprMax/`.

## Run

From the repo root:

```powershell
.\run_gpr_studio.cmd
```

or directly:

```powershell
& "$env:USERPROFILE\miniconda3\envs\gprMax\python.exe" -m streamlit run gpr_studio/app.py
```

Your browser opens the app. Walk the four tabs: **Equipment & survey →
Materials → Geometry & preview → Run & results**.

## Licensing

gprMax is distributed under the GNU GPL v3+ (see `vendor/gprMax/LICENSE`). The
vendored copy here is unmodified; that license governs the `vendor/gprMax/`
subtree.
