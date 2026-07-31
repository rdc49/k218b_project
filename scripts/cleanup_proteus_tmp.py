#!/usr/bin/env python3
"""Delete orphaned /tmp/proteus_* scratch directories from the IoA cluster machines.

Why this exists
-----------------------------------------------------------------------
IT (Neil) reported cap* machines running out of /tmp space, with hundreds
of "proteus" directories not being cleared (see IT_email.txt). Root cause,
confirmed by reading the PROTEUS source: this project's fiducial config
uses `atmos_clim.module = "agni"`, and AGNI creates a fresh scratch working
directory for *every single radiative-transfer call* (i.e. every coupling
iteration, of every grid-sweep case) via `create_tmp_folder()`
(PROTEUS/src/proteus/utils/helper.py:119), which falls back to
`/tmp/proteus_<random>/` whenever `$TMPDIR` is unset (it always is here).
`atmos_clim/agni.py` (line ~634) never removes that directory afterwards
-- unlike the sibling `janus.py` module, which does the equivalent thing
but calls `shutil.rmtree()` when it's done (janus.py:255-256). A second,
once-per-case leak of the same kind happens at case startup
(`utils/coupler.py:1696`, `dirs['temp'] = create_tmp_folder()`) -- grepping
the rest of the codebase shows `dirs['temp']` is never read again anywhere,
so that directory is orphaned from the moment it's created.

Confirmed live via SSH on 2026-07-29 across the machines currently running
our grid-sweep batches: cap001a had 568 `/tmp/proteus_*` dirs (55% of a
16G /tmp), cap003d had 735 (73% of 16G), cap004d had 905 (37% of 32G), and
cap005a (running two concurrent batches, 128 workers) had 3852 (78% of
32G). Individual directories sampled were mostly ~17MB. Some dated back to
August 2025 -- this leak predates this project's grid sweep; the sweep's
14-machine, weeks-long, many-iterations-per-case usage just exercises it
far harder than a normal single PROTEUS run would.

This script is the stopgap fix on our side (deleting what's already been
leaked, daily) while the actual code-level fix -- adding the missing
`shutil.rmtree(io_dir)` to agni.py, matching janus.py -- is a separate,
not-yet-done change to PROTEUS itself.

Safety: why this can't disrupt a running simulation
-----------------------------------------------------------------------
A case's real, checkpointed state (interior snapshots, runtime_helpfile,
logs, etc.) always lives under PROTEUS's own output directory on NFS
(PROTEUS/output/<batch>/case_NNNNNN/ or raw_grid_output/<batch>/case_NNNNNN/),
never under /tmp -- so nothing this script touches can destroy actual
simulation progress. The only real risk is deleting a `/tmp/proteus_*`
directory while AGNI is still actively writing into it mid-iteration
(which would fail/retry that one iteration, not lose overall case
progress). This is guarded against with an age cutoff (`--min-age-minutes`,
default 60): a single AGNI radiative-transfer call takes at most a few
minutes, so anything older than an hour is certainly orphaned, not in
active use. Directories are also filtered to ones owned by the invoking
user (`-user "$(id -un)"`), so this can never touch another user's files
on these shared machines even by accident.

Scheduled daily via cron (see CLAUDE.md, "Scheduled cleanup via cron"), so
like harvest_completed_cases.py this never lets an unexpected exception
propagate uncaught -- every run is wrapped in a try/except that prints a
timestamped start/finish/failure banner.

Usage:
    python scripts/cleanup_proteus_tmp.py [--dry-run] [--min-age-minutes N]

Example:
    python scripts/cleanup_proteus_tmp.py --dry-run
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import harvest_completed_cases as hc  # noqa: E402  (reuse DEFAULT_MACHINES/SSH conventions)

DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"
DEFAULT_MIN_AGE_MINUTES = 60
DEFAULT_MACHINES = hc.DEFAULT_MACHINES
DEFAULT_SSH_TIMEOUT = hc.DEFAULT_SSH_TIMEOUT


def build_find_command(min_age_minutes: float, dry_run: bool) -> str:
    """Build the remote find command.

    Restricted to direct children of /tmp (-mindepth/-maxdepth 1), owned by
    the invoking user, named proteus_*, of type directory, and older than
    min_age_minutes. -print lists what matched (used for both the dry-run
    preview and, in the real-delete case, as a record of what was removed).
    """
    base = (
        "find /tmp -mindepth 1 -maxdepth 1 -type d -name 'proteus_*' "
        f'-user "$(id -un)" -mmin +{min_age_minutes} -print'
    )
    if dry_run:
        return base
    return base + " -exec rm -rf {} +"


def cleanup_machine(
    machine: str, min_age_minutes: float, ssh_timeout: float, dry_run: bool
) -> tuple[str, int | None, str | None]:
    """Run the cleanup (or preview) on one machine over SSH.

    Returns (machine, count_of_dirs_matched_or_None_on_failure, error_or_None).
    """
    remote_cmd = build_find_command(min_age_minutes, dry_run)
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o", f"ConnectTimeout={ssh_timeout}",
                "-o", "BatchMode=yes",
                machine,
                remote_cmd,
            ],
            capture_output=True,
            text=True,
            timeout=ssh_timeout + 30,
        )
    except subprocess.TimeoutExpired:
        return machine, None, "SSH timed out"

    if result.returncode == 255:
        return machine, None, "SSH connection failed (unreachable)"

    matched = [line for line in result.stdout.splitlines() if line.strip()]
    # A non-zero, non-255 return can happen if `find` itself hit a
    # transient error (e.g. NFS hiccup on an unrelated /tmp entry); still
    # report whatever it did manage to match rather than treating the
    # whole machine as a hard failure.
    stderr = result.stderr.strip()
    if result.returncode not in (0, None) and not matched and stderr:
        return machine, None, stderr
    return machine, len(matched), None


def cleanup_all(
    machines: tuple[str, ...], min_age_minutes: float, ssh_timeout: float, dry_run: bool
) -> tuple[dict[str, int], dict[str, str]]:
    """Run cleanup_machine across every machine. Returns (counts, errors)."""
    counts: dict[str, int] = {}
    errors: dict[str, str] = {}
    for machine in machines:
        _, count, error = cleanup_machine(machine, min_age_minutes, ssh_timeout, dry_run)
        if error is not None:
            errors[machine] = error
        else:
            counts[machine] = count
    return counts, errors


def print_summary(counts: dict[str, int], errors: dict[str, str], dry_run: bool) -> None:
    verb = "would remove" if dry_run else "removed"
    total = sum(counts.values())
    print(f"\n{'=' * 70}")
    print(f"cleanup_proteus_tmp.py summary ({'DRY RUN' if dry_run else 'LIVE'})")
    print(f"{'=' * 70}")
    for machine in sorted(counts):
        print(f"  {machine:10s}  {verb} {counts[machine]:5d} dir(s)")
    if errors:
        print("\n  Unreachable/errored machines (skipped, not counted above):")
        for machine in sorted(errors):
            print(f"    {machine:10s}  {errors[machine]}")
    print(f"\n  TOTAL {verb}: {total} dir(s) across {len(counts)} reachable machine(s)")
    if errors:
        print(f"  ({len(errors)} machine(s) could not be checked -- see above)")
    print(f"{'=' * 70}")


class Tee:
    """Write to multiple streams at once (mirrors harvest_completed_cases.py's Tee)."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)

    def flush(self):
        for s in self.streams:
            s.flush()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--min-age-minutes",
        type=float,
        default=DEFAULT_MIN_AGE_MINUTES,
        help="Only remove /tmp/proteus_* dirs at least this old, so an in-progress AGNI "
        f"iteration is never touched (default: {DEFAULT_MIN_AGE_MINUTES})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be removed without actually deleting anything",
    )
    parser.add_argument(
        "--machines",
        type=lambda s: tuple(m.strip() for m in s.split(",") if m.strip()),
        default=DEFAULT_MACHINES,
        help=f"Comma-separated cluster machines to clean (default: all {len(DEFAULT_MACHINES)} "
        "known machines, see grid_sweep_cluster_howto.md)",
    )
    parser.add_argument(
        "--ssh-timeout",
        type=float,
        default=DEFAULT_SSH_TIMEOUT,
        help=f"SSH connect timeout in seconds per machine (default: {DEFAULT_SSH_TIMEOUT})",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=DEFAULT_LOG_DIR,
        help=f"Directory to write a per-day log file into, cleanup_proteus_tmp_YYYY-MM-DD.log "
        f"(default: {DEFAULT_LOG_DIR})",
    )
    parser.add_argument(
        "--no-file-log",
        action="store_true",
        help="Don't write to a per-day log file; only print to stdout/stderr",
    )
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

    if not args.no_file_log:
        args.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = args.log_dir / f"cleanup_proteus_tmp_{datetime.now().strftime('%Y-%m-%d')}.log"
        log_file = open(log_path, "a")
        sys.stdout = Tee(sys.stdout, log_file)
        sys.stderr = Tee(sys.stderr, log_file)

    print(f"=== cleanup_proteus_tmp.py run started {datetime.now().isoformat(timespec='seconds')} ===")
    try:
        counts, errors = cleanup_all(args.machines, args.min_age_minutes, args.ssh_timeout, args.dry_run)
        print_summary(counts, errors, args.dry_run)
    except Exception:
        print(
            f"=== cleanup_proteus_tmp.py run FAILED {datetime.now().isoformat(timespec='seconds')} ===",
            file=sys.stderr,
        )
        traceback.print_exc()
        print("=" * 70, file=sys.stderr)
        return 1
    print(f"=== cleanup_proteus_tmp.py run finished {datetime.now().isoformat(timespec='seconds')} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
