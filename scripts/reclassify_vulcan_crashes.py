#!/usr/bin/env python3
"""One-time migration: reclassify bare `2_fatal_crash_main_loop` cases in
simulation_data/ that were actually VULCAN offline-chemistry-step crashes
(see vulcan_chem_funs_race_condition.md), using the exact same
classify_crash_stage()/match_known_signature() logic
analyze_grid_sweep.py and harvest_completed_cases.py now use for newly-
harvested cases -- nothing here reimplements that classification.

Only touches folders whose outcome-tag suffix is EXACTLY the bare
`2_fatal_crash_main_loop` (no signature suffix) -- folders already tagged
with a specific signature (e.g. `..._agni_coupling_deadlock`) are left
untouched, since those are already correctly attributed to a genuine,
different main-loop failure mode, unrelated to this race condition.

Renames only the trailing outcome-tag segment (everything after the last
`__`), preserving the grid_H..._C..._N..._S..._fO2..__from_<batch>_<case>__
prefix exactly -- so provenance and grid position are never altered. Uses
Path.rename() (same parent directory, atomic), not shutil.move().

Defaults to dry-run/report-only. This is a DELIBERATE, stricter deviation
from harvest_completed_cases.py / generate_gapfill_configs.py, which both
actually default to performing their real action (moving directories /
writing configs) -- renaming ~240 already-harvested, finished directories
warrants the stricter default here. Pass --apply to actually rename.

Usage:
    python3 scripts/reclassify_vulcan_crashes.py                # dry run
    python3 scripts/reclassify_vulcan_crashes.py --apply         # do it

Example:
    python3 scripts/reclassify_vulcan_crashes.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import analyze_grid_sweep as ag  # noqa: E402  (needs sys.path set up first)
import harvest_completed_cases as hc  # noqa: E402
import generate_gapfill_configs as ggc  # noqa: E402

BARE_CATEGORY_2_TAG = ag.CATEGORY_LABELS[2]  # "2_fatal_crash_main_loop", exact match only


def find_candidates(dest_dir: Path) -> list[Path]:
    """Every simulation_data/ folder whose outcome tag is exactly the bare
    category-2 label -- reuses generate_gapfill_configs.parse_harvested_folder
    (the same grid_H..._case_...__<tag> parsing used everywhere else in
    this project) rather than reimplementing the name split."""
    out = []
    if not dest_dir.is_dir():
        return out
    for entry in sorted(dest_dir.iterdir()):
        if not entry.is_dir():
            continue
        parsed = ggc.parse_harvested_folder(entry.name)
        if parsed is None:
            continue
        _indices, tag = parsed
        if tag == BARE_CATEGORY_2_TAG:
            out.append(entry)
    return out


def reclassify_one(case_dir: Path) -> tuple[int, str | None, str] | None:
    """Re-derive (category, signature, new_tag) for one candidate directory
    from its proteus_00.log, using the shared, unmodified helpers. Returns
    None if the log is missing/unreadable or has no Traceback at all
    (shouldn't happen for a folder already tagged as a crash -- treated as
    an anomaly and left alone by the caller)."""
    log_path = case_dir / ag.CASE_LOG_NAME
    if not log_path.is_file():
        print(f"warning: no {ag.CASE_LOG_NAME} in {case_dir.name}, leaving as-is", file=sys.stderr)
        return None

    text = log_path.read_text(errors="replace")
    tb_positions = [m.start() for m in re.finditer("Traceback", text)]
    if not tb_positions:
        print(f"warning: no Traceback found in {case_dir.name}'s log, leaving as-is", file=sys.stderr)
        return None
    tb_pos = tb_positions[-1]

    category = ag.classify_crash_stage(text, tb_pos)
    if category == 3:
        # Shouldn't happen: old and new logic agree on 2-vs-3, so a folder
        # already tagged bare category 2 can only re-derive as 2 or 7.
        print(
            f"warning: {case_dir.name} unexpectedly re-derived as category 3 "
            "(observe-step) -- leaving in place, investigate manually",
            file=sys.stderr,
        )
        return None

    exc_type, exc_msg = ag.extract_exception(text, tb_pos)
    signature = ag.match_known_signature(exc_type, exc_msg) if category == 7 else None
    new_tag = hc.outcome_tag(category, signature)  # reuses the exact same tag-builder
    return category, signature, new_tag


def plan_renames(dest_dir: Path) -> tuple[list[Path], list[tuple[Path, Path]]]:
    """(candidates, [(old_path, new_path), ...]) -- candidates is every
    bare-tagged folder found; the rename list only includes folders that
    actually re-derive as category 7 (genuine category-2 folders and
    anomalies are left untouched, absent from the rename list)."""
    candidates = find_candidates(dest_dir)
    renames = []
    for case_dir in candidates:
        result = reclassify_one(case_dir)
        if result is None:
            continue
        category, _signature, new_tag = result
        if category != 7:
            continue  # confirmed genuinely category 2 -- no change
        prefix, sep, _old_tag = case_dir.name.rpartition("__")
        assert sep, f"unexpected folder name shape: {case_dir.name}"
        new_path = case_dir.with_name(f"{prefix}__{new_tag}")
        renames.append((case_dir, new_path))
    return candidates, renames


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dest", type=Path, default=hc.DEFAULT_SIMULATION_DATA,
        help=f"simulation_data/ directory to scan (default: {hc.DEFAULT_SIMULATION_DATA})",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually perform the renames. Without this flag, only reports "
        "what would be renamed (default: report-only).",
    )
    args = parser.parse_args()

    candidates, renames = plan_renames(args.dest)
    n_stayed_or_skipped = len(candidates) - len(renames)

    print("=" * 70)
    print(f"Scanned {len(candidates)} bare '{BARE_CATEGORY_2_TAG}' folder(s) in {args.dest}")
    print(f"  -> re-derived as genuinely category 2, or skipped as anomalous: {n_stayed_or_skipped}")
    print(f"  -> re-derived as category 7 (VULCAN atmos_chem crash): {len(renames)}")
    print("-" * 70)

    for old_path, new_path in renames:
        action = "renaming" if args.apply else "[dry-run] would rename"
        print(f"{action}: {old_path.name}")
        print(f"       -> {new_path.name}")
        if args.apply:
            if new_path.exists():
                print(
                    f"warning: destination already exists, not overwriting: {new_path}",
                    file=sys.stderr,
                )
                continue
            old_path.rename(new_path)

    print("=" * 70)
    if not args.apply:
        print(f"Dry run only -- re-run with --apply to actually rename {len(renames)} folder(s).")
    else:
        print(f"Renamed {len(renames)} folder(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
