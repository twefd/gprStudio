"""Run gprMax from gprStudio and post-process the results.

Responsibilities:
* write the generated ``.in`` file into a per-project folder,
* shell out to the already-installed gprMax solver (geometry-only / single
  A-scan / full B-scan),
* build the B-scan image by reusing the repo's own ``tools`` post-processing,
* honour the standing preference: after a B-scan merge, delete the individual
  per-trace ``.out`` files, keeping the merged file and the PNG.
"""

from __future__ import annotations

import os
import subprocess
import sys
import glob
import re
import shutil
import time
from pathlib import Path
from typing import Callable, Iterator

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# Repo root = parent of the gpr_studio package. The gprMax solver is vendored
# under vendor/gprMax, so GPRMAX_ROOT is where ``gprMax`` / ``tools`` import from.
REPO_ROOT = Path(__file__).resolve().parent.parent
GPRMAX_ROOT = REPO_ROOT / "vendor" / "gprMax"


def _has_inplace_gprmax() -> bool:
    """True if gprMax's extension is built inside the vendored tree *for this
    platform*. The committed ``.pyd`` are Windows-only, so on Linux (e.g.
    Streamlit Cloud) only a freshly built ``.so`` counts — otherwise those stale
    Windows binaries would wrongly select the vendored tree and shadow the
    pip-installed, correctly-compiled gprMax.
    """
    import importlib.machinery
    d = GPRMAX_ROOT / "gprMax"
    return any(list(d.glob("fields_updates_ext*" + suf))
               for suf in importlib.machinery.EXTENSION_SUFFIXES)


# Working directory for the ``python -m gprMax`` subprocess. Run from the
# vendored tree when its extensions are built there (local dev); otherwise run
# from the repo root — which contains no ``gprMax`` package — so the pip-installed
# gprMax (compiled into site-packages, e.g. on Streamlit Community Cloud) is used
# and the uncompiled vendored source can never shadow it.
GPRMAX_CWD = str(GPRMAX_ROOT) if _has_inplace_gprmax() else str(REPO_ROOT)
PROJECTS_DIR = Path(__file__).resolve().parent / "projects"


def env_python() -> str:
    """Python interpreter to run gprMax with (the one running this app)."""
    return sys.executable


def project_dir(name: str) -> Path:
    d = PROJECTS_DIR / _safe(name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "scene"


def write_infile(text: str, name: str) -> Path:
    """Write ``.in`` text into projects/<name>/<name>.in and return the path."""
    d = project_dir(name)
    in_path = d / f"{_safe(name)}.in"
    in_path.write_text(text, encoding="utf-8")
    return in_path


# --------------------------------------------------------------------------- #
# GPU (CUDA) acceleration
# --------------------------------------------------------------------------- #
def gpu_status() -> dict:
    """Report whether gprMax's CUDA GPU solver can be used here.

    gprMax accelerates the FDTD loops on an NVIDIA GPU via ``pycuda``, which
    JIT-compiles the CUDA kernels with ``nvcc`` at run time. So a usable setup
    needs three things: a CUDA GPU (seen via ``nvidia-smi``), the ``pycuda``
    module in the solver's Python env, and ``nvcc`` (the CUDA Toolkit) on PATH.

    Returns a dict: ``cards`` [(id, name, mem_MiB)], ``pycuda`` bool, ``nvcc``
    bool, ``ready`` bool, and a human ``message``.
    """
    cards: list[tuple[int, str, str]] = []
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run(
                [smi, "--query-gpu=index,name,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=8)
            for line in out.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2 and parts[0].isdigit():
                    cards.append((int(parts[0]), parts[1],
                                  parts[2] if len(parts) > 2 else "?"))
        except Exception:  # noqa: BLE001 - detection must never raise
            pass

    have_pycuda = False
    try:
        r = subprocess.run(
            [env_python(), "-c", "import pycuda.driver as d; d.init(); print('ok')"],
            capture_output=True, text=True, timeout=25, cwd=str(GPRMAX_ROOT))
        have_pycuda = r.returncode == 0 and "ok" in r.stdout
    except Exception:  # noqa: BLE001
        pass

    have_nvcc = shutil.which("nvcc") is not None
    ready = bool(cards) and have_pycuda and have_nvcc

    if ready:
        msg = "GPU ready."
    elif not cards:
        msg = "No NVIDIA GPU detected (nvidia-smi not available)."
    else:
        missing = []
        if not have_pycuda:
            missing.append("`pycuda` (pip install pycuda)")
        if not have_nvcc:
            missing.append("CUDA Toolkit / `nvcc` on PATH")
        msg = "GPU found but not usable yet — missing: " + ", ".join(missing)
    return {"cards": cards, "pycuda": have_pycuda, "nvcc": have_nvcc,
            "ready": ready, "message": msg}


def gpu_utilization(device: int = 0) -> dict | None:
    """Live GPU utilisation + memory for one CUDA device, via ``nvidia-smi``.

    Returns ``{'util': %, 'mem_used': MiB, 'mem_total': MiB}`` or ``None`` if
    unavailable. Cheap enough (~tens of ms) to poll a few times a second while a
    run is in progress.
    """
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        out = subprocess.run(
            [smi, "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits", "-i", str(device)],
            capture_output=True, text=True, timeout=4)
        line = out.stdout.strip().splitlines()[0]
        util, used, total = (int(p.strip()) for p in line.split(","))
        return {"util": util, "mem_used": used, "mem_total": total}
    except Exception:  # noqa: BLE001 - telemetry, never fatal
        return None


def run_gprmax(in_path: Path, n_traces: int | None = None,
               geometry_only: bool = False,
               on_line: Callable[[str], None] | None = None,
               gpu: int | None = None) -> int:
    """Run gprMax on ``in_path``.  Streams output lines to ``on_line``.

    ``gpu`` selects the CUDA device id to run the FDTD solver on (``None`` =
    CPU/OpenMP). Returns the process exit code.
    """
    cmd = [env_python(), "-m", "gprMax", str(in_path)]
    if n_traces and n_traces > 1:
        cmd += ["-n", str(n_traces)]
    if geometry_only:
        cmd += ["--geometry-only"]
    if gpu is not None:
        cmd += ["-gpu", str(gpu)]

    proc = subprocess.Popen(
        cmd, cwd=GPRMAX_CWD, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        if on_line:
            on_line(line.rstrip("\n"))
    proc.wait()
    return proc.returncode


# --------------------------------------------------------------------------- #
# Parallel B-scan (task farm)
# --------------------------------------------------------------------------- #
def auto_workers(n_traces: int, cap: int = 8) -> int:
    """A sensible default worker count: capped, and never more than traces/cores.

    Beyond ~8 workers these small 2D models saturate memory bandwidth rather
    than cores, so returns are flat — hence the cap.
    """
    cores = os.cpu_count() or 4
    return max(1, min(cap, cores, max(1, n_traces)))


def gpu_worker_cap(mem_mib: int | None = None) -> int:
    """Max concurrent GPU worker processes, limited by GPU memory (~1.3 GiB ea)."""
    if not mem_mib:
        return 4
    return max(1, min(6, mem_mib // 1300))


def auto_gpu_workers(n_traces: int, mem_mib: int | None = None) -> int:
    """Default GPU worker count. One process leaves the GPU underused, so a few
    concurrent workers (each with ``--geometry-fixed``) overlap the per-trace CPU
    setup and multiplex the GPU. 5 is a good all-round default: it's the sweet
    spot on small 2D models and, on large/fine grids where the GPU is
    compute-saturated, extra workers simply idle without hurting. Still capped by
    GPU memory.
    """
    return max(1, min(5, gpu_worker_cap(mem_mib), max(1, n_traces)))


def _count_traces(base: Path, n_traces: int) -> int:
    files = [f for f in glob.glob(str(base) + "[0-9]*.out") if "_merged" not in f]
    return min(len(files), n_traces)


def run_bscan_parallel(in_path: Path, n_traces: int, workers: int,
                       on_progress: Callable[[int, int], None] | None = None,
                       poll: float = 0.4,
                       gpu: int | None = None) -> tuple[int, str]:
    """Run a B-scan as a task farm: split traces across ``workers`` processes.

    Each worker runs a contiguous chunk with ``-restart`` + ``--geometry-fixed``
    (geometry is built once per worker; only the antenna moves), sharing the CPU
    via ``OMP_NUM_THREADS = cores / workers``. Produces the same per-trace
    ``.out`` files as a sequential ``-n`` run, so the usual merge works
    unchanged. Returns (returncode, combined_log_tail).

    When ``gpu`` is set, each worker also gets ``-gpu <id>`` so the FDTD solve
    runs on that CUDA device. A single GPU process leaves the card underused, so
    a few workers overlap the per-trace CPU setup and multiplex the GPU.
    """
    base = in_path.with_suffix("")
    # Clear any stale trace files so progress + merge are clean.
    for f in glob.glob(str(base) + "[0-9]*.out"):
        try:
            os.remove(f)
        except OSError:
            pass

    cores = os.cpu_count() or 4
    workers = max(1, min(workers, n_traces))
    threads_per = max(1, cores // workers)
    per = -(-n_traces // workers)  # ceil division -> contiguous chunks

    procs, logs, logpaths = [], [], []
    for w in range(workers):
        start = 1 + w * per
        count = min(per, n_traces - (start - 1))
        if count <= 0:
            break
        env = dict(os.environ)
        env["OMP_NUM_THREADS"] = str(threads_per)
        logpath = Path(str(base) + f"_w{w}.log")
        logf = open(logpath, "w", encoding="utf-8")
        logs.append(logf)
        logpaths.append(logpath)
        cmd = [env_python(), "-m", "gprMax", str(in_path), "-n", str(count),
               "-restart", str(start), "--geometry-fixed"]
        if gpu is not None:
            cmd += ["-gpu", str(gpu)]
        procs.append(subprocess.Popen(cmd, cwd=GPRMAX_CWD, env=env,
                                      stdout=logf, stderr=subprocess.STDOUT))

    while any(p.poll() is None for p in procs):
        if on_progress:
            on_progress(_count_traces(base, n_traces), n_traces)
        time.sleep(poll)
    rcs = [p.wait() for p in procs]
    for logf in logs:
        logf.close()
    if on_progress:
        on_progress(_count_traces(base, n_traces), n_traces)

    # Collect log tails (useful if a worker failed).
    tails = []
    for lp in logpaths:
        try:
            tails.append(f"--- {lp.name} ---\n" + lp.read_text(encoding="utf-8")[-1500:])
        except OSError:
            pass
        try:
            lp.unlink()
        except OSError:
            pass
    return (max(rcs) if rcs else 1), "\n".join(tails)


# --------------------------------------------------------------------------- #
# Post-processing
# --------------------------------------------------------------------------- #
def _get_output_data(out_path: Path, rx: int, component: str):
    """Load a single receiver component (+ dt) from a gprMax .out HDF5 file.

    Self-contained h5py read — a reimplementation of gprMax's
    ``tools.outputfiles_merge.get_output_data`` — so post-processing never
    imports the gprMax package. On a pip-installed deploy (e.g. Streamlit Cloud)
    that import could resolve to the uncompiled vendored source and fail.
    """
    import h5py
    with h5py.File(str(out_path), "r") as f:
        dt = float(f.attrs["dt"])
        grp = f.get(f"/rxs/rx{rx}")
        if grp is None:
            raise KeyError(f"No receiver rx{rx} in {out_path}")
        if component not in grp:
            avail = ", ".join(grp.keys())
            raise KeyError(f"{component!r} not in {out_path} (available: {avail})")
        return np.array(grp[component]), dt


def _merge_out_files(base: str) -> Path:
    """Merge per-trace .out files (base<k>.out) into base_merged.out.

    Self-contained reimplementation of gprMax's
    ``tools.outputfiles_merge.merge_files``: each receiver output is stacked
    across traces (column = trace order) into one HDF5 B-scan, no gprMax import.
    """
    import h5py
    outputfile = base + "_merged.out"
    files = [fn for fn in glob.glob(base + "[0-9]*.out") if "_merged" not in fn]
    files.sort(key=lambda fn: int(re.search(r"(\d+)\.out$", fn).group(1)))
    n = len(files)
    with h5py.File(outputfile, "w") as fout:
        for col, fn in enumerate(files):
            with h5py.File(fn, "r") as fin:
                nrx = int(fin.attrs["nrx"])
                if col == 0:
                    iters = int(fin.attrs["Iterations"])
                    fout.attrs["Title"] = fin.attrs.get("Title", "")
                    fout.attrs["gprMax"] = "gprStudio"
                    fout.attrs["Iterations"] = fin.attrs["Iterations"]
                    fout.attrs["dt"] = fin.attrs["dt"]
                    fout.attrs["nrx"] = nrx
                    for rx in range(1, nrx + 1):
                        grp = fout.create_group(f"/rxs/rx{rx}")
                        for out in fin[f"/rxs/rx{rx}"].keys():
                            grp.create_dataset(
                                out, (iters, n),
                                dtype=fin[f"/rxs/rx{rx}/{out}"].dtype)
                for rx in range(1, nrx + 1):
                    for out in fin[f"/rxs/rx{rx}"].keys():
                        fout[f"/rxs/rx{rx}/{out}"][:, col] = \
                            fin[f"/rxs/rx{rx}/{out}"][:]
    return Path(outputfile)


def plot_ascan(out_path: Path, component: str = "Ez") -> Path:
    """Plot a single A-scan (field vs time) and save a PNG next to the .out."""
    data, dt = _get_output_data(out_path, 1, component)
    t = np.arange(len(data)) * dt * 1e9  # ns
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(t, data, lw=0.9, color="navy")
    ax.set_xlabel("Time [ns]")
    ax.set_ylabel(f"{component} field")
    ax.set_title(f"A-scan — {out_path.stem}")
    ax.grid(True, ls=":", alpha=0.6)
    fig.tight_layout()
    png = out_path.with_suffix(".ascan.png")
    fig.savefig(png, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return png


def make_bscan(base: Path, component: str = "Ez",
               cleanup: bool = True) -> tuple[Path, Path]:
    """Merge per-trace outputs into one B-scan file and render a PNG.

    ``base`` is the path stem shared by the trace files (projects/x/x, so the
    files are x1.out, x2.out, ...).  Returns (merged_out_path, png_path).
    After merging, per-trace files are removed (kept: merged .out + PNG).
    """
    base_str = str(base)
    merged = _merge_out_files(base_str)

    # Render with the standard gray scheme (matches the in-app viewer default).
    png = render_bscan(merged, component, cmap="gray",
                       out_png=Path(base_str + "_bscan.png"))

    if cleanup:
        _cleanup_traces(base)
    return merged, png


def _cleanup_traces(base: Path) -> int:
    """Delete per-trace .out files (base<k>.out), keep *_merged.out."""
    removed = 0
    for f in glob.glob(str(base) + "[0-9]*.out"):
        if "_merged" in f:
            continue
        try:
            os.remove(f)
            removed += 1
        except OSError:
            pass
    return removed


def available_components(out_path: Path) -> list[str]:
    """List the field/current components recorded for receiver 1."""
    import h5py
    try:
        with h5py.File(str(out_path), "r") as f:
            return list(f["/rxs/rx1"].keys())
    except (OSError, KeyError):
        return ["Ez"]


def render_bscan(merged: Path, component: str = "Ez", cmap: str = "gray",
                 gain: float = 1.0, out_png: Path | None = None,
                 x_unit: str = "trace", y_unit: str = "ns",
                 trace_step_m: float | None = None,
                 velocity_m_s: float | None = None) -> Path:
    """Render a B-scan image with adjustable colour map, gain and axis units.

    ``gain`` scales the colour limits (higher = weak reflections brought up).
    ``x_unit`` ∈ {trace, mm, cm, m}; distance uses ``trace_step_m``.
    ``y_unit`` ∈ {ns, mm, cm, m}; depth uses ``velocity_m_s`` (two-way, so
    depth = v·t/2) — an assumed average velocity, since real depth varies by
    layer.
    """
    data, dt = _get_output_data(merged, 1, component)
    nrows, ncols = data.shape
    amp = float(np.amax(np.abs(data))) or 1.0
    vlim = amp / max(gain, 1e-6)

    m_to = {"mm": 1000.0, "cm": 100.0, "m": 1.0}
    if x_unit == "trace" or not trace_step_m:
        x_right, xlabel = ncols, "Trace number"
    else:
        x_right = ncols * trace_step_m * m_to[x_unit]
        xlabel = f"Distance along scan [{x_unit}]"
    if y_unit == "ns" or not velocity_m_s:
        y_bottom, ylabel = nrows * dt * 1e9, "Two-way time [ns]"
    else:
        depth_max_m = velocity_m_s * (nrows * dt) / 2.0
        y_bottom = depth_max_m * m_to[y_unit]
        ylabel = f"Depth [{y_unit}]"

    fig, ax = plt.subplots(figsize=(12, 7))
    im = ax.imshow(data, extent=[0, x_right, y_bottom, 0],
                   interpolation="nearest", aspect="auto", cmap=cmap,
                   vmin=-vlim, vmax=vlim)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(which="both", axis="both", linestyle="-.", alpha=0.4)
    label = ("Field strength [V/m]" if "E" in component else
             "Field strength [A/m]" if "H" in component else "Current [A]")
    fig.colorbar(im, ax=ax, label=label)
    fig.tight_layout()
    if out_png is None:
        out_png = Path(str(merged).replace("_merged.out", "") + "_bscan.png")
    fig.savefig(out_png, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return Path(out_png)


def open_bscan_viewer(merged: Path, component: str = "Ez") -> "subprocess.Popen":
    """Launch gprMax's built-in interactive B-scan viewer in a separate window.

    Runs ``python -m tools.plot_Bscan`` detached. Two things are needed for a
    real window to appear:
    * an *interactive* matplotlib backend — matplotlib silently falls back to
      the non-interactive Agg backend when a process is spawned from a
      non-interactive context, so we force ``MPLBACKEND=TkAgg`` (tkinter ships
      with Python and is the most reliable on Windows);
    * on Windows, its own console (``CREATE_NEW_CONSOLE``) so the child has a
      proper window station to draw on and is not tied to Streamlit's lifetime.

    Note: the window appears on the desktop of whoever launched the Streamlit
    server, so the app must be started from an interactive session (e.g.
    ``run_gpr_studio.cmd``), not a background/service process.
    """
    env = os.environ.copy()
    env["MPLBACKEND"] = "TkAgg"
    cmd = [env_python(), "-m", "tools.plot_Bscan", str(merged), component]
    kwargs: dict = {"cwd": GPRMAX_CWD, "env": env}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    return subprocess.Popen(cmd, **kwargs)


def parse_progress(line: str) -> tuple[int, int] | None:
    """Extract (current_model, total_models) from a gprMax progress line."""
    marker = "Model "
    if marker in line and "/" in line:
        try:
            frag = line.split(marker, 1)[1].split(",", 1)[0]
            cur, tot = frag.split("/")
            return int(cur), int(tot)
        except (ValueError, IndexError):
            return None
    return None
