#!/usr/bin/env python3
"""Generate per-case configs to re-run VULCAN + petitRADTRANS for category-7
(VULCAN-race-crash) cases already sitting in simulation_data/.

Purpose: recovering cases where the main coupled interior-atmosphere loop
already finished successfully but the offline VULCAN chemistry post-
processing step crashed (outcome category 7,
`7_crash_atmos_chem_step[_<signature>]` -- see
vulcan_chem_funs_race_condition.md), now that the underlying shared-file
race conditions have been fixed (chem_funs.py private per-case path,
commit 45177d3 in the VULCAN submodule; the network-definition-file race
this uncovered, fixed separately in
src/proteus/atmos_chem/vulcan.py). Unlike scripts/generate_gapfill_configs.py
(which explicitly never regenerates category-7 cases, since a fresh
single-point config would waste the already-completed main-loop compute),
this script does NOT regenerate the case from scratch: it re-points each
case's own existing `init_coupler.toml` at its own existing
simulation_data/ directory and re-runs only the two post-processing CLI
stages (`proteus offchem` then `proteus observe`) against the physical
output that's already there.

How this works
-----------------------------------------------------------------------
For each targeted case directory:
  1. Load its own `init_coupler.toml` (the frozen config PROTEUS wrote at
     launch time) with tomlkit, preserving comments/formatting.
  2. Override only `[params.out].path` to the case's own *absolute* path
     under simulation_data/. `Proteus`'s directory resolution does
     `os.path.join(proteus_root, 'output', params.out.path)`, and
     `os.path.join` discards everything before an absolute-path component,
     so this makes `offchem`/`observe` operate directly on the existing
     case directory -- no copying, symlinking, or moving needed. Every
     other field is left untouched.
  3. Write the edited config into a fresh timestamped output directory
     under grid_sweep_configs/vulcan_rerun_configs/, partitioned
     round-robin into one subdirectory per target machine.
  4. Write one launch_<machine>.sh per machine: a bounded-concurrency bash
     loop (same MAXJOBS/`wait -n` pattern as generate_gapfill_configs.py's
     LAUNCH_SCRIPT_TEMPLATE), but running two sequential PROTEUS stages per
     case instead of one `proteus grid` call --
     `proteus offchem -c <cfg> && proteus observe -c <cfg>` -- since
     `observe` reads `offchem`'s output and must not run concurrently with
     it for the same case. On success (both stages exit 0 and `observe/`
     is non-empty), the case's outcome-tag directory suffix is renamed
     from `7_crash_atmos_chem_step...` to `1_success_recovered_vulcan_rerun`
     (preserving the `grid_H..._C..._N..._S..._fO2..__from_<batch>_<case>__`
     prefix exactly). On failure, the tag is left unchanged so the case
     remains visibly eligible for a future retry; the failure is recorded
     in `rerun_results.log` for manual follow-up.
  5. Write a CSV manifest (machine,launch_script_path,session_name) for
     scripts/launch_vulcan_reruns.sh to consume.

Nothing is launched automatically -- this script only prepares configs and
per-machine launch scripts. Launching real cluster jobs remains a separate
step via scripts/launch_vulcan_reruns.sh.

Usage:
    python scripts/generate_vulcan_rerun_configs.py [--machines m1,m2,...]
        [--max-jobs 24] [--output-dir DIR] [--report-only] [--limit N]

Example:
    python scripts/generate_vulcan_rerun_configs.py
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

import tomlkit

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import harvest_completed_cases as hc  # noqa: E402  (needs sys.path set up first)

PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_RERUN_ROOT = PROJECT_ROOT / "grid_sweep_configs" / "vulcan_rerun_configs"

CATEGORY7_LABEL = hc.ag.CATEGORY_LABELS[7]
GRID_NAME_RE = re.compile(r"^(grid_H\d+_C\d+_N\d+_S\d+_fO2\d+__from_\S+?__)(.+)$")

# Default idle machines as of 2026-07-30 (see the vulcan-rerun plan) -- always
# re-check busy status live before launching, this list drifts.
DEFAULT_TARGET_MACHINES = (
    "cap001a", "cap001b", "cap001c", "cap001d",
    "cap002b", "cap002c",
    "cap003b", "cap003c", "cap003d",
    "cap004a",
    "cap005a",
)


def find_category7_cases(simulation_data: Path) -> list[Path]:
    """Every simulation_data/ case directory currently tagged category 7
    (bare or with any recognised signature suffix)."""
    if not simulation_data.is_dir():
        return []
    cases = []
    for entry in sorted(simulation_data.iterdir()):
        if not entry.is_dir():
            continue
        m = GRID_NAME_RE.match(entry.name)
        if not m:
            continue
        outcome_tag = m.group(2)
        if outcome_tag == CATEGORY7_LABEL or outcome_tag.startswith(CATEGORY7_LABEL + "_"):
            cases.append(entry)
    return cases


def write_rerun_config(case_dir: Path, machine_dir: Path) -> Path | None:
    """Load case_dir/init_coupler.toml, override [params.out].path to the
    case's own absolute path, write into machine_dir. Returns the written
    path, or None if the case has no init_coupler.toml (shouldn't happen for
    a genuine category-7 case, but don't crash the whole run over one)."""
    src = case_dir / "init_coupler.toml"
    if not src.is_file():
        print(f"  warning: no init_coupler.toml in {case_dir.name}, skipping", file=sys.stderr)
        return None
    doc = tomlkit.parse(src.read_text())
    doc["params"]["out"]["path"] = str(case_dir.resolve())
    out_path = machine_dir / f"{case_dir.name}.toml"
    out_path.write_text(tomlkit.dumps(doc))
    return out_path


LAUNCH_SCRIPT_TEMPLATE = """#!/bin/bash
# Re-run VULCAN (offchem) + petitRADTRANS (observe) for every grid_*.toml
# config in this directory, bounded to at most MAXJOBS concurrent
# case-pipelines. Each pipeline runs `offchem` then `observe` sequentially
# for one case (observe depends on offchem's output), but up to MAXJOBS
# different cases' pipelines run concurrently.
#
# Run this from inside an already-activated proteus conda env, on the
# target machine -- e.g. via a detached tmux session:
#   ssh <machine> "tmux new-session -d -s vulcan_rerun -c /data/rdc49-2/PROTEUS \\
#     && tmux send-keys -t vulcan_rerun 'module load netcdf/4-2025.01' C-m \\
#     && sleep 2 \\
#     && tmux send-keys -t vulcan_rerun 'conda activate proteus' C-m \\
#     && sleep 3 \\
#     && tmux send-keys -t vulcan_rerun 'bash {launch_script_path} <max_jobs>' C-m"
#
# Usage: bash launch_<machine>.sh [max_concurrent_jobs]
set -e
DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
MAXJOBS="${{1:-{default_max_jobs}}}"
cd /data/rdc49-2/PROTEUS

shopt -s nullglob
configs=("$DIR"/grid_*.toml)
if [ ${{#configs[@]}} -eq 0 ]; then
    echo "No grid_*.toml configs found in $DIR"
    exit 1
fi

: > "$DIR/rerun_results.log"

echo "Re-running ${{#configs[@]}} case(s), up to $MAXJOBS concurrently..."
for cfg in "${{configs[@]}}"; do
    while [ "$(jobs -rp | wc -l)" -ge "$MAXJOBS" ]; do
        wait -n
    done
    (
        if (
            nice -n 19 proteus offchem -c "$cfg" &&
            nice -n 19 proteus observe -c "$cfg"
        ) > "${{cfg%.toml}}.rerun.log" 2>&1; then
            echo "SUCCESS $cfg" >> "$DIR/rerun_results.log"
        else
            echo "FAILED $cfg" >> "$DIR/rerun_results.log"
        fi
    ) &
done
wait
echo "All case(s) finished. See $DIR/rerun_results.log for a summary."
echo "Retag successful cases with:"
echo "  python3 /data/rdc49-2/K218b_project/scripts/retag_vulcan_reruns.py $DIR/rerun_results.log"
"""


def write_launch_script(machine_dir: Path, max_jobs: int) -> Path:
    path = machine_dir / f"launch_{machine_dir.name}.sh"
    path.write_text(
        LAUNCH_SCRIPT_TEMPLATE.format(launch_script_path=path, default_max_jobs=max_jobs)
    )
    path.chmod(0o755)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dest", type=Path, default=hc.DEFAULT_SIMULATION_DATA,
        help=f"Where harvested results live (default: {hc.DEFAULT_SIMULATION_DATA})",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Where to write the rerun configs (default: a fresh timestamped "
        f"directory under {DEFAULT_RERUN_ROOT})",
    )
    parser.add_argument(
        "--machines", type=lambda s: tuple(m.strip() for m in s.split(",") if m.strip()),
        default=DEFAULT_TARGET_MACHINES,
        help="Cluster machines to partition cases across (default: the idle "
        f"set as of 2026-07-30, {len(DEFAULT_TARGET_MACHINES)} machines -- "
        "re-verify busy status before launching, see launch_vulcan_reruns.sh)",
    )
    parser.add_argument(
        "--max-jobs", type=int, default=24,
        help="Default MAXJOBS baked into each launch_<machine>.sh (default: 24, "
        "conservative pending memory characterisation -- can be overridden "
        "at launch time as the script's own first argument)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Only take the first N category-7 cases found (for a small pilot run)",
    )
    parser.add_argument(
        "--report-only", action="store_true",
        help="Print the count/breakdown but don't write any config files",
    )
    args = parser.parse_args()

    cases = find_category7_cases(args.dest)
    print("=" * 70)
    print(f"Found {len(cases)} category-7 case(s) in {args.dest}")
    print("=" * 70)
    if not cases:
        return 0

    if args.limit is not None:
        cases = cases[: args.limit]
        print(f"--limit: targeting first {len(cases)} case(s)")

    if args.report_only:
        print("--report-only: not writing any config files.")
        return 0

    if not args.machines:
        print("error: no target machines given", file=sys.stderr)
        return 1

    output_dir = (args.output_dir or (
        DEFAULT_RERUN_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S")
    )).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    machine_dirs = {}
    for machine in args.machines:
        d = output_dir / machine
        d.mkdir(parents=True, exist_ok=True)
        machine_dirs[machine] = d

    written_by_machine: dict[str, list[Path]] = {m: [] for m in args.machines}
    skipped = 0
    for i, case_dir in enumerate(cases):
        machine = args.machines[i % len(args.machines)]
        out = write_rerun_config(case_dir, machine_dirs[machine])
        if out is None:
            skipped += 1
            continue
        written_by_machine[machine].append(out)

    manifest_path = output_dir / "manifest.csv"
    with manifest_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["machine", "launch_script_path", "session_name"])
        for machine, configs in written_by_machine.items():
            if not configs:
                continue
            launch_path = write_launch_script(machine_dirs[machine], args.max_jobs)
            writer.writerow([machine, str(launch_path), f"vulcan_rerun_{machine}"])

    total_written = sum(len(v) for v in written_by_machine.values())
    print(f"\nWrote {total_written} config(s) across {len(args.machines)} machine(s) to: {output_dir}")
    if skipped:
        print(f"Skipped {skipped} case(s) with no init_coupler.toml (see warnings above)")
    for machine, configs in written_by_machine.items():
        if configs:
            print(f"  {machine}: {len(configs)} case(s)")
    print(f"\nManifest written to: {manifest_path}")
    print("Launch across all machines with:")
    print(f"  bash scripts/launch_vulcan_reruns.sh {manifest_path}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
