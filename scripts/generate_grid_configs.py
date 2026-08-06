#!/usr/bin/env python3
"""Generate a full-sweep reference TOML and its 16 batch-split `proteus grid`
configs for a new fO2 x H x C x N x S parameter sweep of this project.

Why this exists
-----------------------------------------------------------------------
The original sweep's `grid_sweep_configs/full_parameter_sweep.toml` and its
16 `batch_configs/batch01.toml`..`batch16.toml` were hand/ad-hoc generated
by a previous session -- each commit message that introduced or resplit
them claims the batches were "verified programmatically" to exactly
partition the full grid with zero overlap, but no such script was ever
committed, so that verification isn't reproducible and the process had to
be redone by hand each time the batch count changed. This script replaces
that gap for good: axis values and the split rule are the only inputs, the
16 batch configs (and the full-sweep reference file) are generated
deterministically, and the partition is asserted -- not just claimed --
before anything is written.

Split rule (matches the original sweep's scheme exactly, see CLAUDE.md and
grid_sweep_cluster_howto.md for the batch-sizing rationale: 64 points/batch
so every "typical" 48-core cluster machine is kept fully busy for a batch's
whole duration, while 16 independent batches are enough to occupy all 14
known machines at once with 2 spare):
    fO2_shift_IW  -- split into 4 singleton groups (one value/batch-group)
    H_budget      -- split into 2 groups (low half / high half)
    C_budget      -- split into 2 groups (low half / high half)
    N_budget      -- kept full (all 4 values) in every batch
    S_budget      -- kept full (all 4 values) in every batch
giving 4 x 2 x 2 = 16 batches x (1 x 2 x 2 x 4 x 4 =) 64 points = 1024.
Batch numbering follows batch_number = f*4 + hh*2 + ch + 1 (1-indexed),
where f/hh/ch are each axis's group index (ascending value order) --
verified against the original batch01.toml/batch02.toml/batch09.toml,
which follow this identical formula.

Namespacing
-----------------------------------------------------------------------
This script is written for the "orange" sweep (see CLAUDE.md / the
war-plan-orange skill) but the axis values and output prefix are just
constants below, so it's reusable for a future third sweep by editing
SWEEP_NAME/AXIS_VALUES and rerunning. Every output is namespaced by
SWEEP_NAME so a new sweep never collides with an existing one:
    grid_sweep_configs/<name>_full_parameter_sweep.toml
    grid_sweep_configs/<name>_batch_configs/batch_<name>NN.toml
Batch filenames deliberately start with the literal string "batch" (not
"<name>_batch...") because scripts/harvest_completed_cases.py,
scripts/generate_gapfill_configs.py, and scripts/generate_grid_status.py
all discover batch configs via a `batch*.toml` glob -- see
discover_batches() in harvest_completed_cases.py.

Usage:
    python3 scripts/generate_grid_configs.py [--check-only] [--force]

Example:
    python3 scripts/generate_grid_configs.py
"""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

REF_CONFIG = "input/nogit_k218b_project/k218b_fiducial.toml"

# TOML dotted-key path for each axis, matching k218b_fiducial.toml's own
# nesting (same as harvest_completed_cases.AXES).
AXIS_KEYS = {
    "H": "planet.elements.H_budget",
    "C": "planet.elements.C_budget",
    "N": "planet.elements.N_budget",
    "S": "planet.elements.S_budget",
    "fO2": "outgas.fO2_shift_IW",
}

# Axes split into groups (one value/batch-group for fO2, low/high halves for
# H and C); axes kept at their full 4-value range in every batch.
SPLIT_AXES = ("fO2", "H", "C")
FULL_AXES = ("N", "S")
N_GROUPS = {"fO2": 4, "H": 2, "C": 2}

# --------------------------------------------------------------------------
# The "orange" sweep definition -- second, narrower-parameter-space sweep,
# see CLAUDE.md / the war-plan-orange skill for why these ranges were chosen
# (the original sweep's ~26/759 successful-case hit rate against
# chisq_madhu_2023_reduced < 2 was too low; this narrows toward the region
# that produced most of those good fits).
# --------------------------------------------------------------------------
SWEEP_NAME = "orange"
AXIS_VALUES = {
    "H": [30000.0, 40000.0, 50000.0, 60000.0],
    "C": [5e-4, 5e-3, 5e-2, 5e-1],
    "N": [5e-4, 5e-3, 5e-2, 5e-1],
    "S": [5e-4, 5e-3, 5e-2, 5e-1],
    "fO2": [-4.0, -3.0, -2.0, -1.0],
}


def fmt(v: float) -> str:
    """Plain decimal float formatting matching the original batch configs'
    style (e.g. 0.0005, not 5e-04; 30000.0, not 3e+04)."""
    return repr(float(v))


def group_values(values: list[float], n_groups: int) -> list[list[float]]:
    """Split a sorted ascending value list into n_groups equal contiguous
    chunks (e.g. 4 values / 2 groups -> [[v0,v1],[v2,v3]])."""
    values = sorted(values)
    assert len(values) % n_groups == 0, (
        f"{len(values)} values doesn't divide evenly into {n_groups} groups"
    )
    chunk = len(values) // n_groups
    return [values[i * chunk : (i + 1) * chunk] for i in range(n_groups)]


def axis_table(key: str, values: list[float]) -> str:
    values_str = ",".join(fmt(v) for v in values)
    return f'["{key}"]\n    method = "direct"\n    values = [{values_str}]\n'


def full_sweep_toml(sweep_name: str, axis_values: dict[str, list[float]]) -> str:
    n_points = 1
    for vs in axis_values.values():
        n_points *= len(vs)
    lines = [
        f"# Config file for the '{sweep_name}' parameter sweep of the K218b project.",
        "#",
        f"# Full factorial grid: 4 (H_budget) x 4 (C_budget) x 4 (N_budget) x",
        f"# 4 (S_budget) x 4 (fO2_shift_IW) = {n_points} points. This is NOT meant to",
        "# be submitted as a single `proteus grid` run across the whole cluster at",
        f"# once. It is split into 16 batches of 64 points each in {sweep_name}_batch_configs/",
        f"# (batch_{sweep_name}01.toml .. batch_{sweep_name}16.toml), split by fO2_shift_IW",
        "# (4 values) x H_budget low/high half (2 groups) x C_budget low/high half",
        "# (2 groups), each batch keeping the full N/S axes -- generated by",
        "# scripts/generate_grid_configs.py. See CLAUDE.md and",
        "# grid_sweep_cluster_howto.md for the batch-sizing rationale (unchanged from",
        "# the original sweep) and the war-plan-orange skill for why this sweep's",
        "# parameter ranges were narrowed relative to the original. This file is kept",
        "# as the reference definition of the full sweep; submit the batch files",
        "# instead.",
        "",
        f'output = "k218b_project_{sweep_name}_sweep/"',
        'symlink = ""',
        "",
        f'ref_config = "{REF_CONFIG}"',
        "",
        "use_slurm = false",
        "max_jobs = 500",
        "max_days = 1",
        "max_mem  = 3",
        "",
    ]
    for axis in ("H", "C", "N", "S", "fO2"):
        lines.append(axis_table(AXIS_KEYS[axis], axis_values[axis]))
    return "\n".join(lines)


def batch_toml(
    sweep_name: str,
    batch_num: int,
    n_batches: int,
    axis_subset: dict[str, list[float]],
    group_descr: str,
) -> str:
    n_points = 1
    for vs in axis_subset.values():
        n_points *= len(vs)
    lines = [
        f"# Batch {batch_num} of {n_batches} -- K218b '{sweep_name}' parameter sweep",
        f"# Full sweep definition: ../{sweep_name}_full_parameter_sweep.toml",
        f"# This batch: {group_descr}",
        f"# Grid size: 1 (fO2) x 2 (H_budget) x 2 (C_budget) x 4 (N_budget) x "
        f"4 (S_budget) = {n_points} points",
        "#",
        "# Generated by scripts/generate_grid_configs.py -- batch-sizing rationale",
        "# (64 points/batch to keep every typical cluster machine fully busy, 16",
        "# batches to occupy up to all 14 known machines at once) is unchanged from",
        "# the original sweep; see CLAUDE.md and grid_sweep_cluster_howto.md.",
        "#",
        '# `symlink` is intentionally left blank (disabled) -- PROTEUS\'s Grid.__init__',
        "# (manage.py ~line 158) calls os.symlink(symlink_dir, self.outdir) where",
        "# self.outdir always has a trailing slash, which os.symlink rejects with",
        "# FileNotFoundError whenever the link doesn't already exist (confirmed via a",
        "# real launch attempt on the original sweep, not specific to this batch or",
        "# sweep). Until that's fixed upstream, real data lands directly under",
        "# PROTEUS/output/<name>/; harvest per-case with",
        "# scripts/harvest_completed_cases.py once checked over -- see CLAUDE.md.",
        "",
        f'output = "k218b_project_{sweep_name}_sweep_batch_{sweep_name}{batch_num:02d}/"',
        'symlink = ""',
        "",
        f'ref_config = "{REF_CONFIG}"',
        "",
        "use_slurm = false",
        "max_jobs = 500",
        "max_days = 1",
        "max_mem  = 3",
        "",
    ]
    for axis in ("H", "C", "N", "S", "fO2"):
        lines.append(axis_table(AXIS_KEYS[axis], axis_subset[axis]))
    return "\n".join(lines)


def build_batches(
    axis_values: dict[str, list[float]],
) -> list[tuple[int, dict[str, list[float]], str]]:
    """Return [(batch_num, {axis: values_subset}, description), ...] for all
    16 batches, in the same fO2-outer / H-middle / C-inner nesting order the
    original sweep used (verified against batch01/02/09.toml)."""
    fo2_groups = group_values(axis_values["fO2"], N_GROUPS["fO2"])
    h_groups = group_values(axis_values["H"], N_GROUPS["H"])
    c_groups = group_values(axis_values["C"], N_GROUPS["C"])
    n_full = sorted(axis_values["N"])
    s_full = sorted(axis_values["S"])

    batches = []
    for f, fo2_vals in enumerate(fo2_groups):
        for hh, h_vals in enumerate(h_groups):
            for ch, c_vals in enumerate(c_groups):
                batch_num = f * 4 + hh * 2 + ch + 1
                subset = {
                    "fO2": fo2_vals,
                    "H": h_vals,
                    "C": c_vals,
                    "N": n_full,
                    "S": s_full,
                }
                half = lambda vals, i: "low half" if i == 0 else "high half"
                descr = (
                    f"fO2_shift_IW = {fmt(fo2_vals[0])} ; "
                    f"H_budget = {half(h_vals, hh)} {h_vals} ; "
                    f"C_budget = {half(c_vals, ch)} {c_vals}"
                )
                batches.append((batch_num, subset, descr))
    return batches


def verify_partition(axis_values: dict[str, list[float]], batches: list) -> None:
    """Assert the batches exactly partition the full Cartesian product of
    axis_values: zero overlap, full coverage. Raises AssertionError with a
    descriptive message on any mismatch."""
    full_points = set(
        itertools.product(
            axis_values["H"], axis_values["C"], axis_values["N"],
            axis_values["S"], axis_values["fO2"],
        )
    )
    seen: set[tuple] = set()
    for batch_num, subset, _ in batches:
        batch_points = set(
            itertools.product(
                subset["H"], subset["C"], subset["N"], subset["S"], subset["fO2"],
            )
        )
        overlap = seen & batch_points
        assert not overlap, f"batch {batch_num} overlaps previous batches: {overlap}"
        seen |= batch_points
    missing = full_points - seen
    extra = seen - full_points
    assert not missing, f"{len(missing)} grid point(s) not covered by any batch: {missing}"
    assert not extra, f"{len(extra)} point(s) generated that aren't in the full grid: {extra}"
    assert len(seen) == 1024, f"expected 1024 points total, got {len(seen)}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--check-only", action="store_true",
        help="Verify the partition and print the batch breakdown without writing any files",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite existing output files/directory if present (off by default)",
    )
    args = parser.parse_args()

    batches = build_batches(AXIS_VALUES)
    print(f"{SWEEP_NAME}: {len(batches)} batches generated, verifying partition...")
    verify_partition(AXIS_VALUES, batches)
    print("Partition verified: 16 batches, zero overlap, exact 1024-point coverage.")

    for batch_num, subset, descr in batches:
        print(f"  batch_{SWEEP_NAME}{batch_num:02d}: {descr}")

    if args.check_only:
        print("--check-only: not writing any files.")
        return 0

    full_sweep_path = PROJECT_ROOT / "grid_sweep_configs" / f"{SWEEP_NAME}_full_parameter_sweep.toml"
    batch_dir = PROJECT_ROOT / "grid_sweep_configs" / f"{SWEEP_NAME}_batch_configs"

    if full_sweep_path.exists() and not args.force:
        print(f"error: {full_sweep_path} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    if batch_dir.exists() and any(batch_dir.iterdir()) and not args.force:
        print(f"error: {batch_dir} already exists and is non-empty (use --force to overwrite)", file=sys.stderr)
        return 1

    full_sweep_path.parent.mkdir(parents=True, exist_ok=True)
    full_sweep_path.write_text(full_sweep_toml(SWEEP_NAME, AXIS_VALUES))
    print(f"wrote {full_sweep_path}")

    batch_dir.mkdir(parents=True, exist_ok=True)
    for batch_num, subset, descr in batches:
        path = batch_dir / f"batch_{SWEEP_NAME}{batch_num:02d}.toml"
        path.write_text(batch_toml(SWEEP_NAME, batch_num, len(batches), subset, descr))
        print(f"wrote {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
