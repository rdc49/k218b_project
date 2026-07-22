#!/usr/bin/env python3
"""Monitor all grid-sweep batches and harvest completed cases into simulation_data/.

Prints a per-batch status summary (real outcome, not just the on-disk
`status` file -- see below), then moves every case with a genuinely
terminal outcome (success, crash, or externally-killed) out of its batch
and into simulation_data/, renamed to encode its position in the full
1024-point parameter sweep (grid_sweep_configs/full_parameter_sweep.toml),
rather than its batch-local case_NNNNNN number.

Meant to be re-run repeatedly (by hand, cron, or --watch) while batches are
still in progress: only cases that have actually finished get moved, so a
batch with 40 of its 128 cases done can have those 40 harvested while the
other 88 keep running untouched.

Each batch's real data location is resolved the same way scripts/move_sweep.py
does: if the batch config sets `symlink`, the real directory is
raw_grid_output/<output-name>/ (PROTEUS/output/<name> is just a symlink to
it); otherwise it's read directly from PROTEUS/output/<name>/ (older,
non-symlinked configs).

Cross-checking against the log, not just the status file
-----------------------------------------------------------------------
This reuses the log-cross-checking approach from scripts/analyze_grid_sweep.py
(a separate, pre-existing tool, not written by this assistant): the on-disk
`status` file is NOT trusted at face value, because it can be stale or
wrong (most importantly: a case can still be genuinely running with
`status` already claiming "Completed"). For every case with an existing
proteus_00.log, the real outcome is derived from the log itself:

  - a Traceback -> crashed (in the main loop, or in the observe/spectrum
    step if it happened after "Observing the planet" was logged), with the
    exception type/message extracted and matched against
    analyze_grid_sweep's KNOWN_FAILURE_SIGNATURES table of known trip-ups
    (e.g. atmodeller overflow, AGNI coupling deadlock, ...);
  - "Simulation stopped" with no Traceback -> genuine success;
  - neither, but the case is still alive -> still running (regardless of
    what `status` says -- this is the case that matters most: never
    harvest a case that's actually still running);
  - neither, and not alive -> dead with no clean finish and no traceback,
    i.e. killed externally (SIGKILL, OOM, node reboot, ...).

Any disagreement between the on-disk `status` and this log-derived outcome
is printed under "STATUS-FILE DISAGREEMENTS" in the summary every run.

The specific helpers reused from analyze_grid_sweep.py are: read_status,
extract_last_loop, extract_exception, match_known_signature,
status_disagreement, CATEGORY_LABELS, get_pgrep_lines, STALE_LOG_SECS. Its
own aliveness check (bare `case_NNNNNN` pgrep matching) is NOT reused
as-is: it is only safe within a single sweep, but here every batch restarts
its own case numbering from 0, so a bare case-number match could find a
*different* batch's live process by coincidence. This script instead
requires both the batch's own `output` name AND the case number in the
same process command line (matching PROTEUS's own
<output>/cfgs/<case>.toml config-path convention), falling back to the same
log-mtime-freshness heuristic otherwise.

Harvested categories: success, crash (main loop or observe step), and
dead/killed-externally-with-a-log are all terminal -- moved out either way,
tagged with their outcome in the new name. Still-running (whether or not
`status` agrees) and "no log file at all" (ambiguous between never-started
and crashed-before-logging) are never harvested.

New name format:
    grid_H<i>_C<i>_N<i>_S<i>_fO2<i>__from_<batch>_<case>__<outcome>[_<signature>]
where each index is that axis's position (0-3) in full_parameter_sweep.toml's
value list, <outcome> is the analyze_grid_sweep category label (e.g.
1_success, 2_fatal_crash_main_loop), and <signature> (only for crashes with
a recognized signature) is the matched known-failure-signature name, e.g.:
    grid_H0_C2_N1_S3_fO20__from_batch01_case000042__2_fatal_crash_main_loop_atmodeller_overflow
The grid_H.._C.._N.._S.._fO2.. prefix is what identifies the case's position
in the sweep; the rest is provenance/outcome, kept for traceability.

Caveat worth knowing about (not worked around here): `proteus grid`'s own
manager process does one final pass over every case's status file right
when the whole batch finishes (PROTEUS/src/proteus/grid/manage.py, end of
Grid.run()), and raises if a status file is missing. Harvesting a case out
of a batch that finishes its very last case in the same instant this script
runs could theoretically make that final pass crash with a traceback -- by
which point all of that batch's actual simulation work is already done, so
nothing is lost, but the manager process log ends in an exception instead of
a clean finish. Narrow window, cosmetic impact, not otherwise guarded against.

Usage:
    python scripts/harvest_completed_cases.py [--summary-only] [--dry-run]

Example:
    python scripts/harvest_completed_cases.py
"""

from __future__ import annotations

import argparse
import math
import re
import shutil
import sys
import time
import tomllib
from collections import Counter
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import analyze_grid_sweep as ag  # noqa: E402  (needs sys.path set up first)

PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_FULL_SWEEP = PROJECT_ROOT / "grid_sweep_configs" / "full_parameter_sweep.toml"
DEFAULT_BATCH_CONFIGS = PROJECT_ROOT / "grid_sweep_configs" / "batch_configs"
DEFAULT_RAW_GRID_OUTPUT = PROJECT_ROOT / "raw_grid_output"
DEFAULT_PROTEUS_OUTPUT = Path("/data/rdc49-2/PROTEUS/output")
DEFAULT_SIMULATION_DATA = PROJECT_ROOT / "simulation_data"

# Order matches the axes present in every batch/full-sweep config.
AXES = {
    "H": "planet.elements.H_budget",
    "C": "planet.elements.C_budget",
    "N": "planet.elements.N_budget",
    "S": "planet.elements.S_budget",
    "fO2": "outgas.fO2_shift_IW",
}

# Categories from analyze_grid_sweep.CATEGORY_LABELS that represent a
# genuinely finished case, safe to move out of its batch.
HARVESTABLE_CATEGORIES = {1, 2, 3, 6}
# Category 6 covers both "no log at all" (never started / crashed before
# logging -- NOT harvestable, too ambiguous) and "log exists but went
# stale with no traceback" (killed externally -- IS harvestable). The two
# are told apart by whether proteus_00.log exists at all.


def load_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def axis_values(cfg: dict, key: str) -> list[float]:
    """Compute a dimension's sorted value list from a grid config's dimension table."""
    table = cfg[key]
    method = table["method"]
    if method == "direct":
        return sorted(set(float(v) for v in table["values"]))
    if method == "logspace":
        return sorted(
            float(v)
            for v in np.logspace(
                math.log10(table["start"]), math.log10(table["stop"]), table["count"]
            )
        )
    if method == "linspace":
        return sorted(float(v) for v in np.linspace(table["start"], table["stop"], table["count"]))
    if method == "arange":
        vals = list(np.arange(table["start"], table["stop"], table["step"]))
        if not np.isclose(vals[-1], table["stop"]):
            vals.append(table["stop"])
        return sorted(float(v) for v in vals)
    raise ValueError(f"Unknown grid dimension method: {method!r}")


def load_full_grid_axes(full_sweep_path: Path) -> dict[str, list[float]]:
    cfg = load_toml(full_sweep_path)
    return {short: axis_values(cfg, key) for short, key in AXES.items()}


def discover_batches(batch_configs_dir: Path, proteus_output: Path) -> list[tuple[str, str, Path]]:
    """Return [(batch_config_stem, output_name, real_source_dir), ...] for every batch*.toml."""
    batches = []
    for path in sorted(batch_configs_dir.glob("batch*.toml")):
        cfg = load_toml(path)
        output_name = str(cfg["output"]).strip().rstrip("/")
        symlink = str(cfg.get("symlink", "")).strip()
        if symlink.lower() in ("", "none", "false"):
            real_dir = proteus_output / output_name
        else:
            real_dir = Path(symlink)
        batches.append((path.stem, output_name, real_dir))
    return batches


def read_case_params(case_dir: Path) -> dict[str, float] | None:
    """Read the resolved grid-axis parameter values from a case's init_coupler.toml."""
    cfg_path = case_dir / "init_coupler.toml"
    if not cfg_path.is_file():
        return None
    cfg = load_toml(cfg_path)
    try:
        return {
            "H": float(cfg["planet"]["elements"]["H_budget"]),
            "C": float(cfg["planet"]["elements"]["C_budget"]),
            "N": float(cfg["planet"]["elements"]["N_budget"]),
            "S": float(cfg["planet"]["elements"]["S_budget"]),
            "fO2": float(cfg["outgas"]["fO2_shift_IW"]),
        }
    except KeyError:
        return None


def match_index(value: float, values: list[float], rtol: float = 1e-4) -> int | None:
    for i, v in enumerate(values):
        if math.isclose(value, v, rel_tol=rtol, abs_tol=1e-12):
            return i
    return None


def grid_position_name(
    params: dict[str, float],
    full_axes: dict[str, list[float]],
    batch_name: str,
    case_name: str,
    outcome_tag: str,
) -> str | None:
    indices = {}
    for axis, value in params.items():
        idx = match_index(value, full_axes[axis])
        if idx is None:
            return None
        indices[axis] = idx
    return (
        f"grid_H{indices['H']}_C{indices['C']}_N{indices['N']}"
        f"_S{indices['S']}_fO2{indices['fO2']}__from_{batch_name}_{case_name}__{outcome_tag}"
    )


# --------------------------------------------------------------------------
# Cross-batch-safe log/status cross-checking (adapted from analyze_grid_sweep.py)
# --------------------------------------------------------------------------


def case_alive_in_batch(case_name: str, batch_output_name: str, mtime: float, now: float) -> bool:
    """Is this specific batch's case still actually running?

    Unlike analyze_grid_sweep.case_is_alive (bare `case_NNNNNN` pgrep
    match -- correct within a single sweep, but every batch here restarts
    numbering from 0, so a bare match could hit a different batch's live
    process), this requires both the batch's own output name and the case
    number in the same process command line, matching PROTEUS's own
    <output>/cfgs/<case>.toml config-path convention. Falls back to the
    same log-mtime-freshness heuristic as analyze_grid_sweep otherwise,
    which needs no such disambiguation (it's specific to this exact
    case_dir's own log file already).
    """
    for line in ag.get_pgrep_lines("proteus start"):
        if batch_output_name in line and case_name in line:
            return True
    return (now - mtime) < ag.STALE_LOG_SECS


def determine_real_outcome(
    case_dir: Path, batch_output_name: str, now: float
) -> tuple[int, str, str | None, tuple[str, str] | None]:
    """Mirror analyze_grid_sweep.analyze_case's categorisation, cross-batch-safe.

    Returns (category, outcome_description, failure_signature_or_None, status).
    `status` is the raw (code, label) tuple from the status file, or None.
    """
    status = ag.read_status(case_dir)
    log_path = case_dir / ag.CASE_LOG_NAME

    if not log_path.is_file():
        outcome = "no simulation log found (crashed before logging started, or never launched)"
        return 6, outcome, None, status

    text = log_path.read_text(errors="replace")
    mtime = log_path.stat().st_mtime
    alive = case_alive_in_batch(case_dir.name, batch_output_name, mtime, now)

    tb_positions = [m.start() for m in re.finditer("Traceback", text)]
    if tb_positions:
        tb_pos = tb_positions[-1]
        exc_type, exc_msg = ag.extract_exception(text, tb_pos)
        loop_at_crash = ag.extract_last_loop(text, before_pos=tb_pos)
        loop_str = f"loop {loop_at_crash}" if loop_at_crash is not None else "before loop 1"
        observe_pos = text.find("Observing the planet")
        category = 3 if (observe_pos != -1 and observe_pos < tb_pos) else 2
        sig = ag.match_known_signature(exc_type, exc_msg)
        exc_label = sig or exc_type or "unrecognized exception"
        stage = "observe/spectrum step" if category == 3 else "main loop"
        outcome = f"crashed in {stage} ({exc_label}) @ {loop_str}"
        return category, outcome, sig, status

    if "Simulation stopped" in text:
        loop_final = ag.extract_last_loop(text)
        reason = ""
        if status and "Completed" in status[1]:
            m = re.search(r"Completed\s*\((.*)\)", status[1])
            reason = m.group(1) if m else ""
        outcome = "success (main loop + observe completed"
        outcome += f", {reason}" if reason else ""
        outcome += f", last loop {loop_final})" if loop_final is not None else ")"
        return 1, outcome, None, status

    if alive:
        loop_now = ag.extract_last_loop(text)
        loop_str = f"loop {loop_now}" if loop_now is not None else "unknown loop"
        if status and "Completed" in status[1]:
            outcome = (
                f"still RUNNING at {loop_str} (post-solidification), but on-disk status "
                f'already reads "{status[1]}" -- stale, do not trust'
            )
            return 5, outcome, None, status
        return 4, f"still RUNNING at {loop_str}", None, status

    age_str = ag.format_age(now - mtime)
    status_str = status[1] if status else "none"
    outcome = (
        f"dead, no traceback, no clean finish -- log stale for {age_str} "
        f"(on-disk status: {status_str}); likely killed externally"
    )
    return 6, outcome, None, status


def outcome_tag(category: int, signature: str | None) -> str:
    label = ag.CATEGORY_LABELS[category]
    return f"{label}_{signature}" if signature else label


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def print_summary(
    batch_categories: dict[str, Counter], disagreements: list[tuple[str, str, str]]
) -> None:
    print("=" * 70)
    print("Batch status summary (real outcome, cross-checked against each case's log)")
    print("=" * 70)
    grand_total: Counter = Counter()
    for batch_name, counts in batch_categories.items():
        total = sum(counts.values())
        if total == 0:
            print(f"{batch_name}: not launched yet (source directory not found)")
            continue
        parts = ", ".join(f"{label}={n}" for label, n in counts.most_common())
        print(f"{batch_name} ({total} cases): {parts}")
        grand_total.update(counts)
    print("-" * 70)
    total = sum(grand_total.values())
    if total:
        parts = ", ".join(f"{label}={n}" for label, n in grand_total.most_common())
        print(f"TOTAL ({total} cases): {parts}")
    print("-" * 70)
    print("STATUS-FILE DISAGREEMENTS (on-disk `status` vs. log-derived outcome):")
    if not disagreements:
        print("  (none -- on-disk status files agree with the log-derived outcome everywhere)")
    else:
        for batch_name, case_name, msg in disagreements:
            print(f"  {batch_name}/{case_name}: {msg}")
    print("=" * 70)


def scan_batches(
    batches: list[tuple[str, str, Path]], now: float
) -> tuple[dict[str, Counter], list[tuple[str, str, str]], dict[str, list[tuple[Path, int, str, str | None]]]]:
    """Classify every case in every batch. Returns (category tallies per batch,
    status disagreements, {batch_name: [(case_dir, category, outcome, signature), ...]})."""
    batch_categories: dict[str, Counter] = {}
    disagreements: list[tuple[str, str, str]] = []
    batch_cases: dict[str, list[tuple[Path, int, str, str | None]]] = {}

    for batch_name, output_name, source_dir in batches:
        counts: Counter = Counter()
        batch_categories[batch_name] = counts
        batch_cases[batch_name] = []
        if not source_dir.is_dir():
            continue

        case_dirs = sorted(
            p for p in source_dir.iterdir() if p.is_dir() and p.name.startswith("case_")
        )
        for case_dir in case_dirs:
            category, outcome, signature, status = determine_real_outcome(
                case_dir, output_name, now
            )
            counts[ag.CATEGORY_LABELS[category]] += 1
            batch_cases[batch_name].append((case_dir, category, outcome, signature))

            disagreement = ag.status_disagreement(status, category)
            if disagreement:
                disagreements.append((batch_name, case_dir.name, disagreement))

    return batch_categories, disagreements, batch_cases


def harvest(
    batches: list[tuple[str, str, Path]],
    full_axes: dict[str, list[float]],
    dest_dir: Path,
    dry_run: bool,
) -> tuple[dict[str, Counter], list[tuple[str, str, str]]]:
    now = time.time()
    batch_categories, disagreements, batch_cases = scan_batches(batches, now)

    for batch_name, cases in batch_cases.items():
        for case_dir, category, outcome, signature in cases:
            if category not in HARVESTABLE_CATEGORIES:
                continue
            if category == 6 and not (case_dir / ag.CASE_LOG_NAME).is_file():
                continue  # no log at all -- never-started/ambiguous, not harvestable

            params = read_case_params(case_dir)
            if params is None:
                print(
                    f"warning: {case_dir} has terminal outcome ({outcome}) but no "
                    "readable init_coupler.toml -- skipping, left in place",
                    file=sys.stderr,
                )
                continue

            tag = outcome_tag(category, signature)
            new_name = grid_position_name(params, full_axes, batch_name, case_dir.name, tag)
            if new_name is None:
                print(
                    f"warning: {case_dir} parameters {params} don't match any "
                    "full-grid axis value within tolerance -- skipping, left in place",
                    file=sys.stderr,
                )
                continue

            dest = dest_dir / new_name
            if dest.exists():
                print(
                    f"warning: destination already exists, not overwriting: {dest} "
                    f"(source: {case_dir})",
                    file=sys.stderr,
                )
                continue

            if dry_run:
                print(f"[dry-run] would move {case_dir} -> {dest}  ({outcome})")
            else:
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(case_dir), str(dest))
                print(f"moved {case_dir} -> {dest}  ({outcome})")

    return batch_categories, disagreements


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--full-sweep",
        type=Path,
        default=DEFAULT_FULL_SWEEP,
        help=f"Full parameter sweep config (default: {DEFAULT_FULL_SWEEP})",
    )
    parser.add_argument(
        "--batch-configs",
        type=Path,
        default=DEFAULT_BATCH_CONFIGS,
        help=f"Directory of batch*.toml configs (default: {DEFAULT_BATCH_CONFIGS})",
    )
    parser.add_argument(
        "--proteus-output",
        type=Path,
        default=DEFAULT_PROTEUS_OUTPUT,
        help=f"PROTEUS output directory, used for non-symlinked batches "
        f"(default: {DEFAULT_PROTEUS_OUTPUT})",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_SIMULATION_DATA,
        help=f"Destination directory for harvested cases (default: {DEFAULT_SIMULATION_DATA})",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print the status summary but don't move anything",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be harvested without actually moving anything",
    )
    parser.add_argument(
        "--watch",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Re-run in a loop every SECONDS instead of once",
    )
    args = parser.parse_args()

    full_axes = load_full_grid_axes(args.full_sweep)
    batches = discover_batches(args.batch_configs, args.proteus_output)
    if not batches:
        print(f"error: no batch*.toml files found under {args.batch_configs}", file=sys.stderr)
        return 1

    def run_once() -> None:
        if args.summary_only:
            categories, disagreements, _ = scan_batches(batches, time.time())
        else:
            categories, disagreements = harvest(batches, full_axes, args.dest, args.dry_run)
        print_summary(categories, disagreements)

    run_once()
    while args.watch:
        time.sleep(args.watch)
        print()
        run_once()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
