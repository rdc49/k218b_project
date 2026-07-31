#!/bin/bash
# Re-run VULCAN (offchem) + petitRADTRANS (observe) for every grid_*.toml
# config in this directory, bounded to at most MAXJOBS concurrent
# case-pipelines. Each pipeline runs `offchem` then `observe` sequentially
# for one case (observe depends on offchem's output), but up to MAXJOBS
# different cases' pipelines run concurrently.
#
# Run this from inside an already-activated proteus conda env, on the
# target machine -- e.g. via a detached tmux session:
#   ssh <machine> "tmux new-session -d -s vulcan_rerun -c /data/rdc49-2/PROTEUS \
#     && tmux send-keys -t vulcan_rerun 'module load netcdf/4-2025.01' C-m \
#     && sleep 2 \
#     && tmux send-keys -t vulcan_rerun 'conda activate proteus' C-m \
#     && sleep 3 \
#     && tmux send-keys -t vulcan_rerun 'bash /data/rdc49-2/K218b_project/grid_sweep_configs/vulcan_rerun_configs/full_run_20260730/cap004a/launch_cap004a.sh <max_jobs>' C-m"
#
# Usage: bash launch_<machine>.sh [max_concurrent_jobs]
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAXJOBS="${1:-6}"
cd /data/rdc49-2/PROTEUS

shopt -s nullglob
configs=("$DIR"/grid_*.toml)
if [ ${#configs[@]} -eq 0 ]; then
    echo "No grid_*.toml configs found in $DIR"
    exit 1
fi

: > "$DIR/rerun_results.log"

echo "Re-running ${#configs[@]} case(s), up to $MAXJOBS concurrently..."
for cfg in "${configs[@]}"; do
    while [ "$(jobs -rp | wc -l)" -ge "$MAXJOBS" ]; do
        wait -n
    done
    (
        (
            nice -n 19 proteus offchem -c "$cfg" &&
            nice -n 19 proteus observe -c "$cfg"
        ) > "${cfg%.toml}.rerun.log" 2>&1
        if [ $? -eq 0 ]; then
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
