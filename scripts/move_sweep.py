#!/usr/bin/env python3
"""Move a completed PROTEUS grid-sweep folder into simulation_data/.

Mirrors the workflow documented in CLAUDE.md under "Moving a completed
sweep into simulation_data/": prints a status summary across all
case_NNNNNN/status files in the sweep, then moves the whole sweep folder
from the PROTEUS output directory into this project's simulation_data/.

Usage:
    python scripts/move_sweep.py <sweep_name> [options]

Example:
    python scripts/move_sweep.py k218b_project_extremity_sweep_4
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections import Counter
from pathlib import Path

DEFAULT_PROTEUS_OUTPUT = Path("/data/rdc49-2/PROTEUS/output")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
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
        "--proteus-output",
        type=Path,
        default=DEFAULT_PROTEUS_OUTPUT,
        help=f"PROTEUS output directory (default: {DEFAULT_PROTEUS_OUTPUT})",
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

    src = args.proteus_output / args.sweep_name
    dest = args.dest / args.sweep_name

    if not src.is_dir():
        print(f"error: sweep folder not found: {src}", file=sys.stderr)
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

    if not args.yes:
        reply = input("Proceed with move? [y/N] ").strip().lower()
        if reply != "y":
            print("Aborted.")
            return 1

    args.dest.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    print(f"Moved to {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
