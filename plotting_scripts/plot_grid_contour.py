#!/usr/bin/env python3
"""Plot one column of simulation_data/grid_master.csv as a discrete heatmap
over two grid axes, with the other three axes held fixed.

Why this exists
-----------------------------------------------------------------------
simulation_data/grid_master.csv already has one row per point in the full
fO2 x H x C x N x S grid (1024 rows), with grid_index_<axis> and physical
budget columns (H_budget/C_budget/N_budget/S_budget/fO2_shift_IW) always
populated, plus ~460 physical-output columns and 4 chisq_* columns
populated only for genuinely successful cases. There was no generic way to
visualise how any one of those output columns varies across two grid axes
while holding the other three fixed -- this script is that tool.

Grid values are sparse: as of this writing only 66/1024 points have
succeeded, so most 2D slices will have several missing cells. This script
renders missing cells as a distinct grey rather than interpolating over
them (no smoothed contourf) -- see the "Plot style" notes below.

Usage:
    python3 plotting_scripts/plot_grid_contour.py \\
        --fix C=0.32 N=0.09 fO2=-3 \\
        --z chisq_madhu_2023

    python3 plotting_scripts/plot_grid_contour.py \\
        --fix H=10000 S=0.04 fO2=-2 \\
        --z T_surf \\
        --output paper/Figures/grid_contours/T_surf_slice.pdf

--fix takes exactly three AXIS=VALUE pairs (axes from H/C/N/S/fO2). VALUE
is a physical value, nearest-matched (within 5% relative tolerance) against
that axis's 4 actual grid values -- it does not need to be typed exactly.
The two axes *not* fixed become the plot's x/y axes, in the fixed order
(H, C, N, S, fO2): whichever of those two comes first is x, the other y.

--z is any column name in grid_master.csv.

Plot style
-----------------------------------------------------------------------
H/C/N/S grid values are evenly log-spaced (e.g. C: 0.0032/0.032/0.32/3.2);
fO2 is evenly linearly spaced. Rather than fight matplotlib's log-scale
pcolormesh edge computation for a 4-point axis, both axes are plotted as
evenly-spaced categorical index positions (0-3) with tick labels showing
the actual physical values -- visually equivalent to a true log axis for
H/C/N/S, and exactly right for fO2, without any interpolation assumption.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_CSV = PROJECT_ROOT / "simulation_data" / "grid_master.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "paper" / "Figures" / "grid_contours"

AXIS_ORDER = ("H", "C", "N", "S", "fO2")
BUDGET_COLUMN = {
    "H": "H_budget",
    "C": "C_budget",
    "N": "N_budget",
    "S": "S_budget",
    "fO2": "fO2_shift_IW",
}
AXIS_LABEL = {
    "H": "H budget [ppmw]",
    "C": "C budget [C/H mass ratio]",
    "N": "N budget [N/H mass ratio]",
    "S": "S budget [S/H mass ratio]",
    "fO2": r"fO2_shift_IW [$\log_{10}$ units rel. IW]",
}
MATCH_TOLERANCE = 0.05  # 5% relative difference


def parse_fix_args(fix_args: list[str]) -> dict[str, float]:
    if len(fix_args) != 3:
        raise SystemExit(
            f"--fix requires exactly 3 AXIS=VALUE pairs, got {len(fix_args)}: {fix_args}"
        )
    fixed: dict[str, float] = {}
    for item in fix_args:
        if "=" not in item:
            raise SystemExit(f"--fix argument {item!r} must be of the form AXIS=VALUE")
        axis, value_str = item.split("=", 1)
        axis = axis.strip()
        if axis not in AXIS_ORDER:
            raise SystemExit(
                f"--fix axis {axis!r} not recognised; must be one of {AXIS_ORDER}"
            )
        if axis in fixed:
            raise SystemExit(f"--fix axis {axis!r} specified more than once")
        try:
            fixed[axis] = float(value_str)
        except ValueError:
            raise SystemExit(f"--fix value {value_str!r} for axis {axis!r} is not a number")
    return fixed


def nearest_grid_index(df: pd.DataFrame, axis: str, target: float) -> tuple[int, float]:
    """Nearest grid_index_<axis> (and its physical value) to `target`,
    within MATCH_TOLERANCE relative difference. Raises SystemExit with the
    full list of valid values otherwise."""
    budget_col = BUDGET_COLUMN[axis]
    index_col = f"grid_index_{axis}"
    axis_values = df[[index_col, budget_col]].drop_duplicates().sort_values(index_col)
    values = axis_values[budget_col].to_numpy()
    indices = axis_values[index_col].to_numpy()

    diffs = np.abs(values - target)
    best = int(np.argmin(diffs))
    best_value = float(values[best])
    best_index = int(indices[best])

    rel_diff = diffs[best] / abs(best_value) if best_value != 0 else diffs[best]
    if rel_diff > MATCH_TOLERANCE:
        valid = ", ".join(f"{v!r}" for v in sorted(values.tolist()))
        raise SystemExit(
            f"--fix {axis}={target!r} did not match any grid value within "
            f"{MATCH_TOLERANCE:.0%} tolerance. Valid {axis} values are: {valid}"
        )
    return best_index, best_value


def format_tick(axis: str, value: float) -> str:
    if axis == "fO2":
        return f"{value:g}"
    return f"{value:.3g}"


def format_fixed_value(axis: str, value: float) -> str:
    if axis == "fO2":
        return f"{value:g}"
    return f"{value:.4g}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--fix",
        nargs="+",
        required=True,
        metavar="AXIS=VALUE",
        help="Exactly 3 of H/C/N/S/fO2, e.g. --fix C=0.32 N=0.09 fO2=-3",
    )
    parser.add_argument(
        "--z", required=True, metavar="COLUMN", help="Column from grid_master.csv to plot as z"
    )
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Path to grid_master.csv")
    parser.add_argument(
        "--output", type=Path, default=None, help="Output figure path (default: auto-computed)"
    )
    args = parser.parse_args()

    fixed = parse_fix_args(args.fix)
    free_axes = [axis for axis in AXIS_ORDER if axis not in fixed]
    assert len(free_axes) == 2
    x_axis, y_axis = free_axes

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

    # Snap each fixed axis to its nearest actual grid value/index.
    fixed_indices: dict[str, int] = {}
    mask = pd.Series(True, index=df.index)
    for axis, target in fixed.items():
        idx, matched_value = nearest_grid_index(df, axis, target)
        fixed_indices[axis] = idx
        print(
            f"Matched --fix {axis}={target!r} -> {BUDGET_COLUMN[axis]}={matched_value!r} "
            f"(grid_index_{axis}={idx})"
        )
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
        z_grid[y_pos, x_pos] = row[args.z]

    n_have = int(np.count_nonzero(~np.isnan(z_grid)))
    n_total = z_grid.size
    print(f"{n_have}/{n_total} grid points in this slice have data for {args.z}")

    cmap = plt.get_cmap("viridis").copy()
    cmap.set_bad(color="0.85")

    fig, ax = plt.subplots(figsize=(6, 5))
    z_masked = np.ma.masked_invalid(z_grid)
    im = ax.imshow(z_masked, origin="lower", cmap=cmap, aspect="auto")

    ax.set_xticks(range(len(x_values)))
    ax.set_xticklabels([format_tick(x_axis, v) for v in x_values])
    ax.set_yticks(range(len(y_values)))
    ax.set_yticklabels([format_tick(y_axis, v) for v in y_values])
    ax.set_xlabel(AXIS_LABEL[x_axis])
    ax.set_ylabel(AXIS_LABEL[y_axis])

    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label(args.z)

    fixed_str = ", ".join(
        f"{axis}={format_fixed_value(axis, fixed[axis])}" for axis in AXIS_ORDER if axis in fixed
    )
    ax.set_title(f"K2-18b grid slice: {fixed_str}\nz = {args.z}", fontsize=10)

    fig.tight_layout()

    if args.output is not None:
        output_path = args.output
    else:
        fix_str = "_".join(f"{axis}{fixed_indices[axis]}" for axis in AXIS_ORDER if axis in fixed)
        output_path = DEFAULT_OUTPUT_DIR / f"{args.z}__fix_{fix_str}.pdf"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)
    print(f"Saved: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
