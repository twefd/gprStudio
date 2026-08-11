"""Render a Scene as a labelled cross-section for previewing before simulation.

Depth is drawn increasing downward, distances in centimetres, materials colour
coded with a legend, and the antenna's start position + scan path overlaid.
Returns a matplotlib Figure so the Streamlit app can display it and scripts can
save it to PNG.
"""

from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Circle, Ellipse, Polygon
from matplotlib.lines import Line2D

from .materials import Material
from .model import Scene, Survey
from .infile import Layout, content_depth, diagonal_corners, oval_centers

M2MM = 1000.0


def render(scene: Scene, survey: Survey,
           materials: dict[str, Material]) -> "plt.Figure":
    lay = Layout(scene, survey, materials)

    # Draw in a surface-relative frame: x from 0..scan region, depth downward.
    x_span_m = lay.width - 2 * lay.pad + survey.tx_rx_offset_m
    depth_span_m = content_depth(scene, survey)

    fig, ax = plt.subplots(figsize=(11, 6))
    used_for_legend: dict[str, str] = {}

    def color(name: str) -> str:
        mat = materials.get(name)
        return mat.color if mat else "#cccccc"

    def register(name: str) -> None:
        mat = materials.get(name)
        if mat:
            used_for_legend[name] = mat.label

    # --- layers ---
    depth_cursor = 0.0
    n = len(scene.layers)
    for i, layer in enumerate(scene.layers):
        top = depth_cursor
        bottom = depth_span_m if i == n - 1 else depth_cursor + layer.thickness_m
        ax.add_patch(Rectangle(
            (0, top * M2MM), x_span_m * M2MM, (bottom - top) * M2MM,
            facecolor=color(layer.material), edgecolor="none", zorder=1))
        register(layer.material)
        depth_cursor += layer.thickness_m

    # --- voids (hatched only for low-permittivity defects, not solid blocks) --
    def is_defect(name: str) -> bool:
        mat = materials.get(name)
        return mat is not None and mat.er < 2.0

    for v in scene.voids:
        ax.add_patch(Rectangle(
            (v.x_m * M2MM, v.depth_m * M2MM),
            v.width_m * M2MM, v.height_m * M2MM,
            facecolor=color(v.material), edgecolor="k", lw=0.8,
            hatch="////" if is_defect(v.material) else None, zorder=3))
        register(v.material)

    # --- bars / conduits ---
    for b in scene.bars:
        if b.grout_material and b.grout_diameter_m > 0:
            ax.add_patch(Circle(
                (b.x_m * M2MM, b.depth_m * M2MM),
                (b.diameter_m / 2) * M2MM,
                facecolor=color(b.material), edgecolor="k", lw=0.8, zorder=3))
            ax.add_patch(Circle(
                (b.x_m * M2MM, b.depth_m * M2MM),
                (b.grout_diameter_m / 2) * M2MM,
                facecolor=color(b.grout_material), edgecolor="none", zorder=4))
            register(b.material)
            register(b.grout_material)
        else:
            ax.add_patch(Circle(
                (b.x_m * M2MM, b.depth_m * M2MM),
                (b.diameter_m / 2) * M2MM,
                facecolor=color(b.material), edgecolor="k", lw=0.8, zorder=3))
            register(b.material)

    # --- rebar rows ---
    for row in scene.rebar_rows:
        register(row.material)
        for k in range(row.count):
            xc = row.x_start_m + k * row.spacing_m
            ax.add_patch(Circle(
                (xc * M2MM, row.depth_m * M2MM),
                (row.diameter_m / 2) * M2MM,
                facecolor=color(row.material), edgecolor="k", lw=0.8, zorder=3))

    # --- diagonal cracks / voids ---
    for d in scene.diagonals:
        register(d.material)
        pts = [(x * M2MM, dep * M2MM) for x, dep in diagonal_corners(d)]
        ax.add_patch(Polygon(pts, closed=True, facecolor=color(d.material),
                             edgecolor="k", lw=0.8, hatch="////", zorder=3))

    # --- oval voids (hollow-core / ducts) ---
    for o in scene.ovals:
        register(o.material)
        for cx, cd in oval_centers(o):
            ax.add_patch(Ellipse(
                (cx * M2MM, cd * M2MM), o.width_m * M2MM, o.height_m * M2MM,
                facecolor=color(o.material), edgecolor="k", lw=0.8, zorder=3))

    # --- surface line + antenna + scan path ---
    ax.axhline(0, color="k", lw=1.5, zorder=5)
    ax.annotate("", xy=(survey.scan_length_m * M2MM, -depth_span_m * M2MM * 0.06),
                xytext=(0, -depth_span_m * M2MM * 0.06),
                arrowprops=dict(arrowstyle="->", color="crimson", lw=1.8),
                zorder=6)
    ax.text(survey.scan_length_m * M2MM / 2, -depth_span_m * M2MM * 0.10,
            "scan direction", color="crimson", ha="center", va="bottom",
            fontsize=9)
    # antenna markers at the start position
    ax.plot(0, 0, marker="v", color="crimson", markersize=12, zorder=7)
    ax.plot(survey.tx_rx_offset_m * M2MM, 0, marker="v", color="darkorange",
            markersize=10, zorder=7)

    # --- axes cosmetics ---
    ax.set_xlim(-20, x_span_m * M2MM + 20)
    ax.set_ylim(depth_span_m * M2MM, -depth_span_m * M2MM * 0.15)
    ax.set_xlabel("Distance along scan [mm]")
    ax.set_ylabel("Depth [mm]")
    ax.set_title(f"{scene.title}  —  preview (not yet simulated)")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, ls=":", lw=0.5, alpha=0.6)

    # --- legend ---
    handles = [Line2D([0], [0], marker="s", ls="", markerfacecolor=color(n),
                      markeredgecolor="k", markersize=10, label=lbl)
               for n, lbl in used_for_legend.items()]
    handles.append(Line2D([0], [0], marker="v", ls="", markerfacecolor="crimson",
                          markeredgecolor="crimson", markersize=10,
                          label="Tx (source)"))
    handles.append(Line2D([0], [0], marker="v", ls="", markerfacecolor="darkorange",
                          markeredgecolor="darkorange", markersize=10,
                          label="Rx (receiver)"))
    ax.legend(handles=handles, loc="center left", bbox_to_anchor=(1.01, 0.5),
              fontsize=8, frameon=True)

    fig.tight_layout()
    return fig
