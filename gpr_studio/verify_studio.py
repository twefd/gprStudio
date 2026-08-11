"""Headless end-to-end self-test for gprStudio.

Builds a representative scene, generates the gprMax input file, renders a
preview PNG, then optionally runs gprMax. Usage:

    <env python> -m gpr_studio.verify_studio            # geometry-only + preview
    <env python> -m gpr_studio.verify_studio --bscan    # + short B-scan run

Run from the repo root so ``gprMax`` and ``tools`` import.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GPRMAX_ROOT = REPO_ROOT / "vendor" / "gprMax"
for _p in (REPO_ROOT, GPRMAX_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from gpr_studio.materials import default_library
from gpr_studio.model import Scene, Survey, Layer, RebarRow, Bar, VoidBox
from gpr_studio import infile, preview, runner


def demo_scene() -> Scene:
    return Scene(
        name="verify_demo",
        title="Verify: slab over sand, rebar row + conduit + void",
        layers=[Layer("dry_concrete", 0.25), Layer("dry_sand", 0.20)],
        rebar_rows=[RebarRow(depth_m=0.06, diameter_m=0.016, spacing_m=0.15,
                             count=4, x_start_m=0.08, material="steel")],
        bars=[Bar(x_m=0.45, depth_m=0.12, diameter_m=0.05, material="pvc")],
        voids=[VoidBox(x_m=0.25, depth_m=0.18, width_m=0.08, height_m=0.03,
                       material="air")],
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bscan", action="store_true",
                    help="also run the full-coverage B-scan (slower)")
    ap.add_argument("--step-mm", type=float, default=10.0,
                    help="trace spacing in mm for the --bscan run")
    args = ap.parse_args()

    mats = default_library()
    scene = demo_scene()
    # Scan the FULL model width so the antenna passes over every object.
    scan_len = infile.full_scan_length(scene)
    survey = Survey(
        equipment_key="gp8000", center_freq_hz=2.0e9, tx_rx_offset_m=0.04,
        scan_length_m=scan_len, trace_step_m=args.step_mm / 1000.0,
        max_depth_m=0.40, dx_m=infile.suggest_dx(2.0e9, scene, mats),
    )

    print(f"[1] dx = {survey.dx_m*1000:.2f} mm, "
          f"scan length = {scan_len*100:.1f} cm, traces = {survey.num_traces}")

    text = infile.generate(scene, survey, mats)
    in_path = runner.write_infile(text, scene.name)
    print(f"[2] wrote input file: {in_path}")

    fig = preview.render(scene, survey, mats)
    prev_png = in_path.with_suffix(".preview.png")
    fig.savefig(prev_png, dpi=120, bbox_inches="tight")
    print(f"[3] wrote preview: {prev_png}")

    print("[4] running gprMax --geometry-only …")
    rc = runner.run_gprmax(in_path, geometry_only=True,
                           on_line=lambda l: None)
    if rc != 0:
        print(f"    FAILED: gprMax geometry-only exited {rc}")
        return rc
    print("    geometry built OK")

    if args.bscan:
        print(f"[5] running B-scan ({survey.num_traces} traces) …")
        rc = runner.run_gprmax(in_path, n_traces=survey.num_traces,
                               on_line=lambda l: None)
        if rc != 0:
            print(f"    FAILED: B-scan exited {rc}")
            return rc
        merged, png = runner.make_bscan(in_path.with_suffix(""))
        print(f"    merged: {merged}")
        print(f"    B-scan image: {png}")

    print("\nSELF-TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
