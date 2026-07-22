#!/usr/bin/env python3
"""Move a completed PROTEUS grid-sweep folder into simulation_data/.

Mirrors the workflow documented in CLAUDE.md under "Moving a completed
sweep into simulation_data/": prints a status summary across all
case_NNNNNN/status files in the sweep, then moves the whole sweep folder
into this project's simulation_data/.

Batch configs under grid_sweep_configs/batch_configs/ set `symlink` to an
absolute path under raw_grid_output/, so PROTEUS writes the real data
there directly and only leaves a symlink under PROTEUS/output/<name>/.
This script's default source is therefore raw_grid_output/, not
PROTEUS/output/ -- moving the real directory out from under a symlink
(rather than the symlink itself) is what actually relocates the data; a
naive move of a symlink just relocates the symlink. After moving, any
matching symlink left behind under PROTEUS/output/<name> (pointing at the
directory just moved) is cleaned up so it doesn't dangle.

Older sweeps launched without a `symlink` config (real data written
directly under PROTEUS/output/) are also supported: if the sweep isn't
found under raw_grid_output/, the script falls back to treating
PROTEUS/output/<name> as the real directory, exactly as before.

Usage:
    python scripts/move_sweep.py <sweep_name> [options]

Example:
    python scripts/move_sweep.py k218b_project_main_parameter_sweep_batch01
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections import Counter
from pathlib import Path

DEFAULT_PROTEUS_OUTPUT = Path("/data/rdc49-2/PROTEUS/output")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RAW_GRID_OUTPUT = PROJECT_ROOT / "raw_grid_output"
DEFAULT_SIMULATION_DATA = PROJECT_ROOT / "simulation_data"


def status_summary(sweep_dir: Path) -> Counter:
    """Tally the text status label for each case_NNNNNN/status file.

    Each status file holds two lines: a numeric code and a text label
    (e.g. "1\nRunning"). Only the text label is reported.
    """
    counts: Counter = Counter()
    case_dirs = sorted(p for p in sweep_dir.iterdir() if p.is_dir())
    for case_dir in case_dirs:
        status_file = case_dir / "status"
        if status_file.is_file():
            lines = [line.strip() for line in status_file.read_text().splitlines() if line.strip()]
            label = lines[-1] if lines else "<empty status file>"
            counts[label] += 1
        else:
            counts["<no status file>"] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sweep_name", help="Name of the sweep folder to move")
    parser.add_argument(
        "--raw-grid-output",
        type=Path,
        default=DEFAULT_RAW_GRID_OUTPUT,
        help=f"Staging directory that symlinked batch runs write into "
        f"(default: {DEFAULT_RAW_GRID_OUTPUT})",
    )
    parser.add_argument(
        "--proteus-output",
        type=Path,
        default=DEFAULT_PROTEUS_OUTPUT,
        help=f"PROTEUS output directory, used as a fallback for older "
        f"non-symlinked sweeps and to clean up a dangling symlink after "
        f"moving a symlinked one (default: {DEFAULT_PROTEUS_OUTPUT})",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_SIMULATION_DATA,
        help=f"Destination directory (default: {DEFAULT_SIMULATION_DATA})",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt",
    )
    args = parser.parse_args()

    raw_src = args.raw_grid_output / args.sweep_name
    proteus_src = args.proteus_output / args.sweep_name
    dest = args.dest / args.sweep_name

    symlink_to_clean: Path | None = None
    if raw_src.is_dir() and not raw_src.is_symlink():
        # New-style: real data staged under raw_grid_output/, PROTEUS/output/
        # holds a symlink to it (set via the batch config's `symlink` field).
        src = raw_src
        if proteus_src.is_symlink() and proteus_src.resolve() == raw_src.resolve():
            symlink_to_clean = proteus_src
    elif proteus_src.is_dir() and not proteus_src.is_symlink():
        # Old-style: real data written directly under PROTEUS/output/.
        src = proteus_src
    else:
        print(
            f"error: sweep folder not found under either {raw_src} or {proteus_src}",
            file=sys.stderr,
        )
        return 1

    if dest.exists():
        print(f"error: destination already exists: {dest}", file=sys.stderr)
        return 1

    counts = status_summary(src)
    print(f"Status summary for {src}:")
    for status, n in counts.most_common():
        print(f"  {n:4d}  {status}")

    running = counts.get("Running", 0)
    missing = counts.get("<no status file>", 0)
    if running or missing:
        print(
            f"\nWarning: {running} case(s) still Running and {missing} "
            "case(s) with no status file. Moving anyway is fine if you've "
            "deliberately chosen to (e.g. partial/interim results); "
            "otherwise consider waiting until the sweep is complete."
        )

    size = sum(f.stat().st_size for f in src.rglob("*") if f.is_file())
    print(f"\n{src} -> {dest}  ({size / 1e9:.1f} GB)")
    if symlink_to_clean:
        print(f"Will also remove the now-dangling symlink at {symlink_to_clean}")

    if not args.yes:
        reply = input("Proceed with move? [y/N] ").strip().lower()
        if reply != "y":
            print("Aborted.")
            return 1

    args.dest.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    print(f"Moved to {dest}")

    if symlink_to_clean:
        symlink_to_clean.unlink()
        print(f"Removed dangling symlink at {symlink_to_clean}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
