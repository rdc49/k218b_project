#!/usr/bin/env python3
"""Build simulation_data/grid_master.csv -- one row per point in the full
fO2 x H x C x N x S grid, populated from each finished-main-loop case's
*final* runtime_helpfile.csv row (see "Scope" below for exactly which
outcome categories count as finished).

Why this exists
-----------------------------------------------------------------------
Each case directory in simulation_data/ has its own per-timestep
runtime_helpfile.csv (459 columns -- tab-separated despite the .csv name).
There is no single place to see the final state of every grid point at a
glance; comparing across the sweep otherwise means opening dozens of
individual per-case files by hand. This script builds that one summary
table.

None of runtime_helpfile.csv's 459 columns record the swept input
parameters (H_budget/C_budget/N_budget/S_budget/fO2_shift_IW) -- those
live only in each case's init_coupler.toml / the folder-name-encoded grid
indices. So the master CSV needs extra identifying columns beyond the
459: grid_index_<H,C,N,S,fO2> and the corresponding physical values,
computed directly from grid_sweep_configs/full_parameter_sweep.toml (the
same source scripts/harvest_completed_cases.py and
scripts/generate_gapfill_configs.py already use), not read from any
per-case file -- so they're populated identically for every one of the
1024 rows, whether or not that point has a successful case yet.

Scope: a row is populated by any case whose runtime_helpfile.csv last row
reflects a genuinely finished main coupled loop -- that's "1_success"
cases, and also "7_crash_atmos_chem_step*" cases: for those, the main
loop already finished successfully and only the *offline* VULCAN
atmos-chem post-processing step crashed afterwards (a shared-file race
condition, see vulcan_chem_funs_race_condition.md), so their last
runtime_helpfile.csv row is just as physically meaningful as a genuine
success -- they simply won't have an offchem synthetic spectrum, so
compute_spectral_chisq.py's per-case spectrum lookup naturally skips them
(chisq stays NaN) rather than needing any special-casing here. All other
crashed/killed cases are left blank, same as points never attempted at
all: their last row is wherever the simulation happened to be when it
died, not a finished state. A "source_outcome" column records which of
the two populating buckets ("success" / "vulcan_crashed") a row came
from.

Row structure: always exactly one row per point in the full grid (1024
for the current 4-value-per-axis sweep), in a fixed deterministic order.
Re-running this script rebuilds the file from scratch each time --
simple, always-correct, and consistent with this project's existing
"regeneratable, don't hand-maintain" pattern for other derived files
(GRID_INDEX_LEGEND.txt, the gap-fill config directories).

Reuses (not reimplemented):
  - scripts/harvest_completed_cases.py's load_full_grid_axes() -- axis
    definitions from grid_sweep_configs/full_parameter_sweep.toml.
  - scripts/generate_gapfill_configs.py's AXIS_ORDER, full_grid_indices(),
    GRID_NAME_RE / parse_harvested_folder(), classify_outcome_tag() --
    the same simulation_data/ folder-name parsing and outcome bucketing
    used everywhere else in this project.

Usage:
    python3 scripts/build_grid_summary.py [--dry-run] [--output PATH]

Example:
    python3 scripts/build_grid_summary.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import harvest_completed_cases as hc  # noqa: E402  (needs sys.path set up first)
import generate_gapfill_configs as ggc  # noqa: E402

PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "simulation_data" / "grid_master.csv"

BUDGET_COLUMN = {
    "H": "H_budget",
    "C": "C_budget",
    "N": "N_budget",
    "S": "S_budget",
    "fO2": "fO2_shift_IW",
}

# grid_H0_C0_N0_S0_fO20__from_batch01_case_000000__1_success
PROVENANCE_RE = re.compile(r"__from_(?P<batch>[^_]+)_case_(?P<case>\d+)__")


POPULATING_OUTCOMES = ("success", "vulcan_crashed")


def find_populatable_cases(dest_dir: Path) -> dict[tuple[int, ...], tuple[Path, str]]:
    """{index_tuple: (case_dir, outcome)} for every case in simulation_data/
    whose runtime_helpfile.csv last row reflects a genuinely finished main
    loop -- "1_success" cases, and "7_crash_atmos_chem_step*" cases (main
    loop finished; only the offline VULCAN post-processing step crashed).
    All other crash/kill categories are excluded, same as never-attempted
    points.

    If more than one folder matches the same index tuple: a "success" case
    always wins over a "vulcan_crashed" one for that point (matches this
    project's usual "success always wins" convention, e.g.
    generate_gapfill_configs.py's scan_simulation_data()); between two
    folders in the same bucket, keep the most-recently-modified one and
    warn about the rest.
    """
    resolved: dict[tuple[int, ...], tuple[Path, str]] = {}
    if not dest_dir.is_dir():
        return resolved

    for entry in sorted(dest_dir.iterdir()):
        if not entry.is_dir():
            continue
        parsed = ggc.parse_harvested_folder(entry.name)
        if parsed is None:
            continue
        indices, outcome_tag = parsed
        bucket = ggc.classify_outcome_tag(outcome_tag)
        if bucket not in POPULATING_OUTCOMES:
            continue

        existing = resolved.get(indices)
        if existing is None:
            resolved[indices] = (entry, bucket)
            continue

        existing_entry, existing_bucket = existing
        if bucket == existing_bucket:
            existing_mtime = existing_entry.stat().st_mtime
            new_mtime = entry.stat().st_mtime
            newer, older = (
                (entry, existing_entry) if new_mtime > existing_mtime else (existing_entry, entry)
            )
            print(
                f"warning: multiple {bucket} cases for grid point {indices} -- "
                f"keeping {newer.name} (newer), discarding {older.name}",
                file=sys.stderr,
            )
            resolved[indices] = (newer, bucket)
        elif bucket == "success" and existing_bucket == "vulcan_crashed":
            print(
                f"note: grid point {indices} has both a vulcan_crashed case "
                f"({existing_entry.name}) and a success case ({entry.name}) -- "
                f"using the success case",
                file=sys.stderr,
            )
            resolved[indices] = (entry, "success")
        else:
            # existing is "success", new is "vulcan_crashed" -- success wins, keep existing.
            print(
                f"note: grid point {indices} has both a success case "
                f"({existing_entry.name}) and a vulcan_crashed case "
                f"({entry.name}) -- keeping the success case",
                file=sys.stderr,
            )

    return resolved


def parse_provenance(case_dir_name: str) -> tuple[str, str]:
    """Best-effort (source_batch, source_case_number) from a harvested folder
    name; blank strings if the naming doesn't match the usual pattern."""
    m = PROVENANCE_RE.search(case_dir_name)
    if not m:
        return "", ""
    return m.group("batch"), m.group("case")


def load_last_row(case_dir: Path) -> pd.Series | None:
    """Last row of a case's runtime_helpfile.csv (tab-separated), or None
    if the file is missing, empty, or unreadable."""
    helpfile = case_dir / "runtime_helpfile.csv"
    if not helpfile.is_file():
        print(f"warning: no runtime_helpfile.csv in {case_dir.name}, skipping", file=sys.stderr)
        return None
    try:
        df = pd.read_csv(helpfile, sep="\t")
    except Exception as exc:  # noqa: BLE001 -- report and skip, don't crash the whole run
        print(f"warning: could not read {helpfile}: {exc}, skipping", file=sys.stderr)
        return None
    if df.empty:
        print(f"warning: {helpfile} has no data rows, skipping", file=sys.stderr)
        return None
    return df.iloc[-1]


def build_summary(full_sweep_path: Path, dest_dir: Path) -> pd.DataFrame:
    full_axes = hc.load_full_grid_axes(full_sweep_path)
    all_indices = ggc.full_grid_indices(full_axes)

    # Base rows: ID columns only, for every one of the 1024 points, always.
    id_rows = []
    for indices in all_indices:
        row = {}
        for axis, idx in zip(ggc.AXIS_ORDER, indices):
            row[f"grid_index_{axis}"] = idx
        for axis, idx in zip(ggc.AXIS_ORDER, indices):
            row[BUDGET_COLUMN[axis]] = full_axes[axis][idx]
        id_rows.append(row)
    base_df = pd.DataFrame(id_rows)
    base_df["source_case_dir"] = ""
    base_df["source_batch"] = ""
    base_df["source_case_number"] = ""
    base_df["source_outcome"] = ""

    populatable = find_populatable_cases(dest_dir)

    helpfile_rows: dict[int, pd.Series] = {}
    helpfile_columns: list[str] = []
    for row_idx, indices in enumerate(all_indices):
        entry = populatable.get(indices)
        if entry is None:
            continue
        case_dir, outcome = entry
        last_row = load_last_row(case_dir)
        if last_row is None:
            continue
        base_df.at[row_idx, "source_case_dir"] = case_dir.name
        batch, case_num = parse_provenance(case_dir.name)
        base_df.at[row_idx, "source_batch"] = batch
        base_df.at[row_idx, "source_case_number"] = case_num
        base_df.at[row_idx, "source_outcome"] = outcome
        helpfile_rows[row_idx] = last_row
        # Union of columns across all cases, in first-seen order -- avoids
        # silently dropping columns if a later case's helpfile has extra
        # ones not present in the first case encountered.
        for col in last_row.index:
            if col not in helpfile_columns:
                helpfile_columns.append(col)

    helpfile_df = pd.DataFrame(index=base_df.index, columns=helpfile_columns, dtype=object)
    for row_idx, series in helpfile_rows.items():
        helpfile_df.loc[row_idx] = series

    return pd.concat([base_df, helpfile_df], axis=1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--full-sweep", type=Path, default=hc.DEFAULT_FULL_SWEEP,
        help=f"Full parameter sweep config (default: {hc.DEFAULT_FULL_SWEEP})",
    )
    parser.add_argument(
        "--dest", type=Path, default=hc.DEFAULT_SIMULATION_DATA,
        help=f"Directory of harvested cases to scan (default: {hc.DEFAULT_SIMULATION_DATA})",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help=f"Where to write the master CSV (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the summary counts without writing the output file",
    )
    args = parser.parse_args()

    df = build_summary(args.full_sweep, args.dest)

    total = len(df)
    populated = int((df["source_case_dir"] != "").sum())
    n_success = int((df["source_outcome"] == "success").sum())
    n_vulcan_crashed = int((df["source_outcome"] == "vulcan_crashed").sum())

    print("=" * 70)
    print(
        f"Grid summary: {populated} / {total} points populated "
        f"({n_success} success, {n_vulcan_crashed} vulcan_crashed)"
    )
    print("=" * 70)

    if args.dry_run:
        print("--dry-run: not writing any file.")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
