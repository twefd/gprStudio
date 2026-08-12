# Deploying gprStudio on Streamlit Community Cloud

This gives colleagues a URL they just open — **no setup on their side**. The
repo already contains everything the platform needs:

- `requirements.txt` — installs Streamlit + builds the vendored gprMax from
  source (its Windows `.pyd` don't work on the Linux build host).
- `packages.txt` — `build-essential`, the C/OpenMP compiler gprMax needs.
- `vendor/gprMax/pyproject.toml` — build requirements so the compile works in
  pip's isolated build environment.

## One-time deploy (you, on your account)

1. Go to **https://share.streamlit.io** and sign in with the **GitHub account
   that owns the repo** (`twefd`).
2. The repo is **private**, so authorize Streamlit's GitHub app to access it
   (Streamlit will prompt; grant access to `twefd/gprStudio`).
3. **Create app → Deploy from GitHub** and set:
   - **Repository:** `twefd/gprStudio`
   - **Branch:** `dev`  *(the up-to-date branch; SCC auto-redeploys on push)*
   - **Main file path:** `gpr_studio/app.py`
   - **Advanced settings → Python version:** **3.12** (or 3.11)
4. **Deploy.** The first build takes a few minutes because it compiles gprMax's
   Cython/OpenMP extensions. Watch the build log; once it says the app is
   running, open the URL.
5. **Restrict who can view it** (so it's colleagues-only): in the app's
   settings, set it to private and invite colleagues by email. Then just share
   the URL — they open it and use it, nothing to install.

Updating later: push to `dev` and the app redeploys automatically.

## What works, and the limits to expect

Community Cloud is a small, **CPU-only, shared Linux container** (~1 vCPU,
~1–2.7 GB RAM, ephemeral disk). So:

- ✅ Full UI: equipment/survey, materials, geometry builder + preview,
  A-scans and B-scans, the in-app B-scan image viewer, save/import scenes.
- ⚠️ **CPU only — no GPU.** The "Run on GPU" toggle won't appear; runs use the
  CPU task farm on ~1 core. **Keep models small** (coarse cell size, modest
  scan length / trace count). Fine grids will be slow and can hit the memory
  limit. This host is for light models and sharing/demoing, not the heavy
  fine-grid work — for that, run locally on the GPU box (see main README).
- ⚠️ **Ephemeral storage:** files under `gpr_studio/projects/` reset whenever
  the app sleeps/restarts. Download B-scan images / data you want to keep.
- ⚠️ The **"Open in interactive gprMax viewer"** button needs a desktop and
  won't work here — use the in-app image viewer instead.
- ⚠️ **One shared server:** simultaneous heavy runs from several colleagues
  contend. Fine for occasional use by a small team.

## If a heavier / always-on / GPU deployment is needed later

Containerize on a Linux + CUDA base image and run it on a GPU host (your
workstation or a cloud GPU VM), behind a Cloudflare Tunnel for the URL. That
keeps the GPU speed-ups; ask and I'll add a `Dockerfile` + compose.
