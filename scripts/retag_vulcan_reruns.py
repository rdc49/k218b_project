#!/usr/bin/env python3
"""Retag simulation_data/ cases after a VULCAN+petitRADTRANS rerun.

Purpose: after scripts/generate_vulcan_rerun_configs.py's per-machine
launch_<machine>.sh scripts have re-run `proteus offchem` + `proteus
observe` for a batch of category-7 (`7_crash_atmos_chem_step...`) cases,
this script independently verifies each targeted case's own simulation_data/
directory (not just the shell exit code recorded in rerun_results.log) and:
  - on success (offchem produced a real vulcan.csv result AND observe
    produced at least one spectrum file): renames the case directory's
    outcome-tag suffix from `7_crash_atmos_chem_step...` to
    `1_success_recovered_vulcan_rerun`, preserving the
    `grid_H..._C..._N..._S..._fO2..__from_<batch>_<case>__` prefix exactly
    (same rename mechanism as scripts/reclassify_vulcan_crashes.py:
    Path.rename() on just the trailing segment).
  - on failure: leaves the tag unchanged (still `7_crash_atmos_chem_step...`)
    so the case remains visibly eligible for a future retry; the failure is
    just reported, not retagged.

Verification is done directly against each case's own directory (found via
each grid_*.toml's own [params.out].path, since that's the absolute path
this whole rerun pointed PROTEUS at) rather than trusting the launch
script's own SUCCESS/FAILED log, so this is safe to run even if
rerun_results.log is missing, stale, or was itself interrupted.

Usage:
    python scripts/retag_vulcan_reruns.py <rerun_configs_dir> [--apply]
        [--dest simulation_data/]

<rerun_configs_dir> is the top-level output directory from
generate_vulcan_rerun_configs.py (containing one subdirectory per machine,
each with its own grid_*.toml configs). Defaults to a dry run listing what
would be renamed; pass --apply to actually rename directories.

Example:
    python scripts/retag_vulcan_reruns.py \\
        grid_sweep_configs/vulcan_rerun_configs/20260730_140000 --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import tomlkit

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import harvest_completed_cases as hc  # noqa: E402  (needs sys.path set up first)

CATEGORY7_LABEL = hc.ag.CATEGORY_LABELS[7]
SUCCESS_LABEL = "1_success_recovered_vulcan_rerun"


def case_dir_from_config(config_path: Path) -> Path | None:
    doc = tomlkit.parse(config_path.read_text())
    try:
        path_str = doc["params"]["out"]["path"]
    except KeyError:
        return None
    return Path(str(path_str))


def verify_success(case_dir: Path) -> tuple[bool, str]:
    """Independently check whether this case's offchem+observe rerun
    actually succeeded, by looking at what's on disk rather than trusting
    any shell exit code."""
    offchem_csv = case_dir / "offchem" / "vulcan.csv"
    if not offchem_csv.is_file() or offchem_csv.stat().st_size == 0:
        return False, "offchem/vulcan.csv missing or empty"

    observe_dir = case_dir / "observe"
    if not observe_dir.is_dir() or not any(observe_dir.iterdir()):
        return False, "observe/ missing or empty"

    return True, "ok"


def retag_case(case_dir: Path, apply: bool) -> tuple[bool, str]:
    """Rename case_dir's trailing __<outcome-tag> segment from category 7 to
    SUCCESS_LABEL, preserving everything before the last '__'. Returns
    (renamed, new_path_or_reason)."""
    name = case_dir.name
    prefix, _, outcome_tag = name.rpartition("__")
    if not prefix:
        return False, "directory name doesn't match the expected grid_..._from_..._<tag> pattern"
    if not (outcome_tag == CATEGORY7_LABEL or outcome_tag.startswith(CATEGORY7_LABEL + "_")):
        return False, f"not currently tagged category 7 (tag: {outcome_tag})"

    new_name = f"{prefix}__{SUCCESS_LABEL}"
    new_path = case_dir.parent / new_name
    if apply:
        case_dir.rename(new_path)
    return True, str(new_path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "rerun_configs_dir", type=Path,
        help="Top-level output directory from generate_vulcan_rerun_configs.py",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually rename directories (default: dry run, report only)",
    )
    args = parser.parse_args()

    if not args.rerun_configs_dir.is_dir():
        print(f"error: {args.rerun_configs_dir} is not a directory", file=sys.stderr)
        return 1

    configs = sorted(args.rerun_configs_dir.glob("*/grid_*.toml"))
    if not configs:
        print(f"No grid_*.toml configs found under {args.rerun_configs_dir}", file=sys.stderr)
        return 1

    succeeded, failed, anomalous = [], [], []
    for config_path in configs:
        case_dir = case_dir_from_config(config_path)
        if case_dir is None or not case_dir.is_dir():
            anomalous.append((config_path, "could not resolve case directory from config"))
            continue

        ok, reason = verify_success(case_dir)
        if ok:
            renamed, detail = retag_case(case_dir, args.apply)
            if renamed:
                succeeded.append((case_dir, detail))
            else:
                anomalous.append((case_dir, detail))
        else:
            failed.append((case_dir, reason))

    verb = "Renamed" if args.apply else "Would rename"
    print("=" * 70)
    print(f"{verb} {len(succeeded)} case(s) to {SUCCESS_LABEL}:")
    for case_dir, detail in succeeded:
        print(f"  {case_dir.name} -> {Path(detail).name if args.apply else detail}")

    print(f"\n{len(failed)} case(s) still failing (left tagged category 7 for a future retry):")
    for case_dir, reason in failed:
        print(f"  {case_dir.name}: {reason}")

    if anomalous:
        print(f"\n{len(anomalous)} anomalous case(s) (needs manual look):")
        for case_dir, reason in anomalous:
            print(f"  {case_dir}: {reason}")

    if not args.apply and succeeded:
        print("\nDry run only -- pass --apply to actually rename directories.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
