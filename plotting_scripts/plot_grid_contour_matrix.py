#!/usr/bin/env python3
"""Plot all C(5,2)=10 pairwise 2D slices of simulation_data/grid_master.csv
for one column, arranged in a single 5x2 grid of subplots.

Why this exists
-----------------------------------------------------------------------
plot_grid_contour.py renders one axis pair at a time (--fix 3 axes, plot
the other 2). This script is the "give me all of them at once" version:
for each of the 10 possible pairs among (H, C, N, S, fO2), the other 3
axes are fixed at the project's fiducial values (see k218b_fiducial.toml:
fO2_shift_IW=-3, H_budget=10000 ppmw, C_budget=0.32, N_budget=0.09,
S_budget=0.04), nearest-matched to a grid point exactly like
plot_grid_contour.py's --fix logic. All 10 panels share one colorbar
(common vmin/vmax) so they are directly comparable.

Reuses plot_grid_contour.py's constants and matching/formatting helpers
rather than reimplementing them -- only the per-pair subset/z-grid build
(which isn't factored into a standalone function upstream) is repeated
here, using the same algorithm.

Usage:
    python3 plotting_scripts/plot_grid_contour_matrix.py --z chisq_madhu_2023

    python3 plotting_scripts/plot_grid_contour_matrix.py \\
        --z T_surf \\
        --output paper/Figures/grid_contours/T_surf_matrix.pdf

Panel layout (row-major, 5 rows x 2 cols):
    row 1: (H,C)   (H,N)
    row 2: (H,S)   (H,fO2)
    row 3: (C,N)   (C,S)
    row 4: (C,fO2) (N,S)
    row 5: (N,fO2) (S,fO2)
"""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plot_grid_contour import (  # noqa: E402
    AXIS_LABEL,
    AXIS_ORDER,
    BUDGET_COLUMN,
    DEFAULT_CSV,
    DEFAULT_OUTPUT_DIR,
    format_fixed_value,
    format_tick,
    nearest_grid_index,
)

FIDUCIAL_FIXED = {"fO2": -3.0, "H": 10000.0, "C": 0.32, "N": 0.09, "S": 0.04}

N_ROWS = 5
N_COLS = 2


def build_z_grid(
    df: pd.DataFrame, fixed_indices: dict[str, int], x_axis: str, y_axis: str, z_column: str
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Subset df to the given fixed grid indices and pivot z_column onto the
    (x_axis, y_axis) grid. Mirrors plot_grid_contour.py's main() body."""
    mask = pd.Series(True, index=df.index)
    for axis, idx in fixed_indices.items():
        mask &= df[f"grid_index_{axis}"] == idx
    subset = df[mask]

    x_lookup = (
        subset[[f"grid_index_{x_axis}", BUDGET_COLUMN[x_axis]]]
        .drop_duplicates()
        .sort_values(f"grid_index_{x_axis}")
    )
    y_lookup = (
        subset[[f"grid_index_{y_axis}", BUDGET_COLUMN[y_axis]]]
        .drop_duplicates()
        .sort_values(f"grid_index_{y_axis}")
    )
    x_indices = x_lookup[f"grid_index_{x_axis}"].to_numpy()
    y_indices = y_lookup[f"grid_index_{y_axis}"].to_numpy()
    x_values = x_lookup[BUDGET_COLUMN[x_axis]].to_numpy()
    y_values = y_lookup[BUDGET_COLUMN[y_axis]].to_numpy()

    z_grid = np.full((len(y_indices), len(x_indices)), np.nan)
    for _, row in subset.iterrows():
        xi = int(row[f"grid_index_{x_axis}"])
        yi = int(row[f"grid_index_{y_axis}"])
        x_pos = int(np.where(x_indices == xi)[0][0])
        y_pos = int(np.where(y_indices == yi)[0][0])
        z_grid[y_pos, x_pos] = row[z_column]

    return z_grid, x_values, y_values


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--z", required=True, metavar="COLUMN", help="Column from grid_master.csv to plot as z"
    )
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Path to grid_master.csv")
    parser.add_argument(
        "--output", type=Path, default=None, help="Output figure path (default: auto-computed)"
    )
    args = parser.parse_args()

    if not args.csv.is_file():
        raise SystemExit(f"CSV not found: {args.csv}")
    df = pd.read_csv(args.csv, dtype={"source_case_number": str})

    if args.z not in df.columns:
        raise SystemExit(
            f"--z column {args.z!r} not found in {args.csv}. "
            f"Closest matches: {[c for c in df.columns if args.z.lower() in c.lower()][:10]}"
        )
    if df[args.z].isna().all():
        print(f"warning: column {args.z!r} is entirely NaN across the whole grid", file=sys.stderr)

    pairs = list(itertools.combinations(AXIS_ORDER, 2))
    assert len(pairs) == N_ROWS * N_COLS

    # Pass 1: snap fixed axes, build each panel's z_grid, track global vmin/vmax.
    panels = []
    for x_axis, y_axis in pairs:
        fixed_axes = [axis for axis in AXIS_ORDER if axis not in (x_axis, y_axis)]
        fixed_indices: dict[str, int] = {}
        fixed_values: dict[str, float] = {}
        for axis in fixed_axes:
            idx, matched_value = nearest_grid_index(df, axis, FIDUCIAL_FIXED[axis])
            fixed_indices[axis] = idx
            fixed_values[axis] = matched_value
            print(
                f"[{x_axis}-{y_axis}] Matched {axis}={FIDUCIAL_FIXED[axis]!r} -> "
                f"{BUDGET_COLUMN[axis]}={matched_value!r} (grid_index_{axis}={idx})"
            )

        z_grid, x_values, y_values = build_z_grid(df, fixed_indices, x_axis, y_axis, args.z)
        n_have = int(np.count_nonzero(~np.isnan(z_grid)))
        n_total = z_grid.size
        print(f"[{x_axis}-{y_axis}] {n_have}/{n_total} grid points in this slice have data for {args.z}")

        fixed_str = ", ".join(
            f"{axis}={format_fixed_value(axis, fixed_values[axis])}"
            for axis in AXIS_ORDER
            if axis in fixed_values
        )
        panels.append(
            {
                "x_axis": x_axis,
                "y_axis": y_axis,
                "x_values": x_values,
                "y_values": y_values,
                "z_grid": z_grid,
                "fixed_str": fixed_str,
            }
        )

    all_z = np.concatenate([p["z_grid"].ravel() for p in panels])
    if np.all(np.isnan(all_z)):
        raise SystemExit(f"column {args.z!r} is entirely NaN across every panel; nothing to plot")
    vmin = float(np.nanmin(all_z))
    vmax = float(np.nanmax(all_z))

    # Pass 2: render the 5x2 grid with a shared color scale and colorbar.
    cmap = plt.get_cmap("viridis").copy()
    cmap.set_bad(color="0.85")

    fig, axes = plt.subplots(N_ROWS, N_COLS, figsize=(11, 20), constrained_layout=True)
    im = None
    for panel, ax in zip(panels, axes.ravel()):
        z_masked = np.ma.masked_invalid(panel["z_grid"])
        im = ax.imshow(z_masked, origin="lower", cmap=cmap, aspect="auto", vmin=vmin, vmax=vmax)

        ax.set_xticks(range(len(panel["x_values"])))
        ax.set_xticklabels([format_tick(panel["x_axis"], v) for v in panel["x_values"]])
        ax.set_yticks(range(len(panel["y_values"])))
        ax.set_yticklabels([format_tick(panel["y_axis"], v) for v in panel["y_values"]])
        ax.set_xlabel(AXIS_LABEL[panel["x_axis"]])
        ax.set_ylabel(AXIS_LABEL[panel["y_axis"]])
        ax.set_title(panel["fixed_str"], fontsize=8)

    fig.suptitle(
        f"K2-18b grid pairwise slices: z = {args.z}\n"
        "(other 3 axes fixed at fiducial values per panel, nearest-matched to grid points)",
        fontsize=11,
    )

    cbar = fig.colorbar(im, ax=axes, shrink=0.6, pad=0.02)
    cbar.set_label(args.z)

    if args.output is not None:
        output_path = args.output
    else:
        output_path = DEFAULT_OUTPUT_DIR / f"{args.z}__all_pairs.pdf"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)
    print(f"Saved: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
