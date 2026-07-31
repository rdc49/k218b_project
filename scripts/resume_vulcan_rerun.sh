#!/usr/bin/env bash
#
# resume_vulcan_rerun.sh -- resume a vulcan-rerun machine directory
# (grid_sweep_configs/vulcan_rerun_configs/<run>/<machine>/), skipping any
# grid_*.toml config that already has a corresponding .rerun.log (already
# attempted, whether it succeeded or crashed).
#
# Recovery path for a bug in the original per-machine launch_<machine>.sh
# scripts generated before 2026-07-31: under `set -e`, a crashing case's
# `(offchem && observe)` failure was not isolated inside an if-condition,
# so errexit killed that case's backgrounded subshell before it could log
# "FAILED" -- and worse, when the main loop's `wait -n` reaped that
# non-zero-exit subshell, errexit propagated into the *main script* too,
# silently terminating the whole launcher and abandoning every
# not-yet-dispatched config in that directory. (Fixed in the
# LAUNCH_SCRIPT_TEMPLATE in generate_vulcan_rerun_configs.py for future
# runs, but not retroactive to already-launched scripts.)
#
# This script is idempotent/safe to run repeatedly on the same directory --
# already-attempted configs (anything with a .rerun.log, success or
# failure) are always skipped, so it only ever dispatches genuinely
# never-attempted cases.
#
# Usage: resume_vulcan_rerun.sh <machine_dir> [max_concurrent_jobs]
set -e
DIR="$1"
MAXJOBS="${2:-6}"
if [ -z "$DIR" ]; then
    echo "Usage: resume_vulcan_rerun.sh <machine_dir> [max_concurrent_jobs]" >&2
    exit 1
fi
DIR="$(cd "$DIR" && pwd)"
cd /data/rdc49-2/PROTEUS

shopt -s nullglob
configs=("$DIR"/grid_*.toml)
todo=()
for cfg in "${configs[@]}"; do
    if [ ! -f "${cfg%.toml}.rerun.log" ]; then
        todo+=("$cfg")
    fi
done

if [ ${#todo[@]} -eq 0 ]; then
    echo "Nothing to resume in $DIR (all ${#configs[@]} config(s) already attempted)"
    exit 0
fi

echo "Resuming ${#todo[@]} of ${#configs[@]} case(s) (skipping already-attempted), up to $MAXJOBS concurrently..."
for cfg in "${todo[@]}"; do
    while [ "$(jobs -rp | wc -l)" -ge "$MAXJOBS" ]; do
        wait -n
    done
    (
        if (
            nice -n 19 proteus offchem -c "$cfg" &&
            nice -n 19 proteus observe -c "$cfg"
        ) > "${cfg%.toml}.rerun.log" 2>&1; then
            echo "SUCCESS $cfg" >> "$DIR/rerun_results.log"
        else
            echo "FAILED $cfg" >> "$DIR/rerun_results.log"
        fi
    ) &
done
wait
echo "Resume batch finished. See $DIR/rerun_results.log."
