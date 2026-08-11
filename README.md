# gprStudio

A guided, local web app for modelling real concrete-inspection GPR scenarios.
Build a 2D cross-section (layers + rebar + conduits + voids), preview it, and run
a B-scan that approximates real GPR gear — the **Proceq GP8000** by default.

gprStudio is a [Streamlit](https://streamlit.io) front-end. The heavy lifting —
solving Maxwell's equations with the FDTD method — is done by
[**gprMax**](http://www.gprmax.com), which is **vendored** into this repository
under [`vendor/gprMax/`](vendor/gprMax/) as a self-contained dependency. This
repo is the studio (my work); gprMax is an unmodified upstream dependency living
underneath it, not a fork.

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

## GPU acceleration (optional)

gprMax can run the FDTD solver on an NVIDIA GPU — much faster than CPU for
larger grids. gprStudio surfaces this in **④ Run & results**: when a usable GPU
is detected a **⚡ Run on GPU (CUDA)** toggle appears (with a device picker if
there's more than one card). The toggle only enables when all three of these are
present, which the app checks for you:

1. an NVIDIA CUDA GPU (seen via `nvidia-smi`),
2. the `pycuda` module in the gprMax env, and
3. `nvcc` (the CUDA Toolkit) on `PATH` — `pycuda` JIT-compiles gprMax's CUDA
   kernels with it at run time.

To set it up:

1. Install the [NVIDIA CUDA Toolkit](https://developer.nvidia.com/cuda-downloads)
   and put `nvcc` on your `PATH` (e.g.
   `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\vXX.X\bin`).
2. Install `pycuda` into the gprMax env:
   ```powershell
   & "$env:USERPROFILE\miniconda3\envs\gprMax\python.exe" -m pip install pycuda
   ```
   (On Windows / very recent Python versions there may be no prebuilt wheel —
   install a matching wheel or build it against your CUDA Toolkit.)
3. Back in the app, click **↻ Re-check** on the Run tab.

A GPU run is a single `python -m gprMax <in> -gpu <id>` process (the CPU
task-farm is CPU-only, so it's bypassed on GPU); results are identical to CPU.

## Self-test

A headless end-to-end check (build → preview → geometry-only solve):

```powershell
python -m gpr_studio.verify_studio            # geometry-only + preview
python -m gpr_studio.verify_studio --bscan    # + a short B-scan run
```

## Updating the vendored gprMax

`vendor/gprMax/` is a plain copy of upstream gprMax. To update it, drop in a
newer gprMax source tree (or re-copy from a clone), rebuild the extensions, and
commit. Nothing in `gpr_studio/` needs to change as long as gprMax's public
interface (`python -m gprMax`, the `tools` package) is unchanged.

## Licensing

gprMax is distributed under the GNU GPL v3+ (see `vendor/gprMax/LICENSE`). The
vendored copy here is unmodified; that license governs the `vendor/gprMax/`
subtree.
