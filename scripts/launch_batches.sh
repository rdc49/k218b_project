#!/usr/bin/env bash
#
# launch_batches.sh -- automate launching PROTEUS grid-sweep batches across
# the IoA cluster, following the manual procedure documented in
# grid_sweep_cluster_howto.md ("Steps to launch a sweep"): for each
# machine/config/session assignment, live-check the machine is actually
# free, launch a detached tmux session running the standard
# `module load` -> `cd PROTEUS` -> `conda activate proteus` ->
# `nice -n 19 proteus grid -c <config>` sequence, then confirm real
# simulation progress.
#
# SAFETY: `proteus grid`'s Grid.__init__ unconditionally `shutil.rmtree`s
# an existing, non-empty output directory before recreating it empty --
# there is no check for case_* content, no confirmation prompt beyond a
# hardcoded 4-second sleep, and `proteus grid --dry-run` does NOT protect
# against this (the wipe happens before dry_run is ever consulted). This
# script is the only thing standing between a mistaken re-launch and
# silently destroying an in-progress or finished sweep, so by default it
# refuses to launch into any output directory that already exists and has
# content. Use --force-clobber to override, deliberately, one row at a time.
#
# Usage:
#   scripts/launch_batches.sh [options] <manifest.csv>
#
# Manifest format (comments with '#', blank lines skipped):
#   # machine,config_path (relative to project root, or absolute),session_name
#   cap001c,grid_sweep_configs/batch_configs/batch03.toml,proteus_batch03
#   cap001d,grid_sweep_configs/batch_configs/batch04.toml,proteus_batch04
#   cap005a,grid_sweep_configs/batch_configs/batch13.toml,proteus_batch13
#   cap005a,grid_sweep_configs/batch_configs/batch14.toml,proteus_batch14
#
# A machine appearing more than once in a manifest (e.g. cap005a running
# two sweeps concurrently) is treated as deliberate: the busy-check only
# runs the first time a machine is seen in a given invocation.
#
# Options:
#   --dry-run                   Run every pre-flight check (config
#                                validation, clobber check, busy check) and
#                                print exactly what would be launched,
#                                without ever SSHing a tmux/proteus launch
#                                command.
#   --stagger-seconds N          Delay between launching each row
#                                (default: 15). Launching many machines at
#                                once causes NFS import contention on the
#                                shared conda env (observed 2026-07-27:
#                                some machines took ~4 minutes instead of
#                                ~15-90s to print anything) -- staggering
#                                reduces how many machines hit that at once.
#   --force-clobber              Override the output-directory-exists
#                                refusal. Off by default. Applies per-row;
#                                prints a loud warning whenever it fires.
#   --ssh-timeout N               SSH ConnectTimeout, seconds (default: 6).
#   --confirm-timeout-seconds N  How long the final confirm pass polls a
#                                slow-starting sweep before giving up
#                                (default: 300 -- today's NFS-contention
#                                delay reached ~4 minutes, so this gives
#                                headroom).
#   -h, --help                    Show this help and exit.
#
# Examples:
#   scripts/launch_batches.sh --dry-run manifests/batch03-16.csv
#   scripts/launch_batches.sh --stagger-seconds 20 manifests/batch03-16.csv

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PROTEUS_OUTPUT_DIR="/data/rdc49-2/PROTEUS/output"
PROTEUS_DIR="/data/rdc49-2/PROTEUS"

DRY_RUN=0
STAGGER_SECONDS=15
FORCE_CLOBBER=0
SSH_TIMEOUT=6
CONFIRM_TIMEOUT_SECONDS=300
MANIFEST=""

usage() {
    sed -n '2,/^set -euo pipefail/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'
}

log() {
    printf '%s\n' "$*"
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --stagger-seconds)
            [ $# -ge 2 ] || die "--stagger-seconds requires a value"
            STAGGER_SECONDS="$2"
            shift 2
            ;;
        --force-clobber)
            FORCE_CLOBBER=1
            shift
            ;;
        --ssh-timeout)
            [ $# -ge 2 ] || die "--ssh-timeout requires a value"
            SSH_TIMEOUT="$2"
            shift 2
            ;;
        --confirm-timeout-seconds)
            [ $# -ge 2 ] || die "--confirm-timeout-seconds requires a value"
            CONFIRM_TIMEOUT_SECONDS="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        -*)
            die "Unknown option: $1 (see --help)"
            ;;
        *)
            if [ -n "$MANIFEST" ]; then
                die "Multiple manifest arguments given ('$MANIFEST' and '$1')"
            fi
            MANIFEST="$1"
            shift
            ;;
    esac
done

[ -n "$MANIFEST" ] || { usage; die "No manifest file given"; }
[ -f "$MANIFEST" ] || die "Manifest file not found: $MANIFEST"

if [ "$DRY_RUN" -eq 1 ]; then
    log "[dry-run] no tmux/proteus launch commands will actually be sent"
fi

# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------

ROW_MACHINE=()
ROW_CONFIG_REL=()
ROW_SESSION=()
ROW_OUTPUT=()
ROW_STATUS=()

lineno=0
while IFS= read -r raw_line || [ -n "$raw_line" ]; do
    lineno=$((lineno + 1))
    # strip CR (in case the manifest has DOS line endings) and surrounding whitespace
    line="$(printf '%s' "$raw_line" | tr -d '\r')"
    line="$(printf '%s' "$line" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"

    [ -z "$line" ] && continue
    [[ "$line" == \#* ]] && continue

    IFS=',' read -r f_machine f_config f_session <<<"$line"
    f_machine="$(printf '%s' "$f_machine" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    f_config="$(printf '%s' "$f_config" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    f_session="$(printf '%s' "$f_session" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"

    if [ -z "$f_machine" ] || [ -z "$f_config" ] || [ -z "$f_session" ]; then
        die "Manifest line $lineno: expected 'machine,config_path,session_name', got: $raw_line"
    fi

    ROW_MACHINE+=("$f_machine")
    ROW_CONFIG_REL+=("$f_config")
    ROW_SESSION+=("$f_session")
    ROW_OUTPUT+=("")
    ROW_STATUS+=("")
done <"$MANIFEST"

NUM_ROWS=${#ROW_MACHINE[@]}
[ "$NUM_ROWS" -gt 0 ] || die "Manifest has no launch rows: $MANIFEST"

log "Loaded $NUM_ROWS row(s) from $MANIFEST"
log ""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Extract a bare-string TOML field like: key = "value"
toml_field() {
    local key="$1" file="$2"
    grep -m1 -E "^[[:space:]]*${key}[[:space:]]*=" "$file" 2>/dev/null \
        | sed -E "s/^[^=]*=[[:space:]]*\"([^\"]*)\".*/\1/" \
        || true
}

declare -A MACHINE_CHECKED_THIS_RUN

# ---------------------------------------------------------------------------
# Per-row pre-flight + launch
# ---------------------------------------------------------------------------

for i in "${!ROW_MACHINE[@]}"; do
    machine="${ROW_MACHINE[$i]}"
    config_rel="${ROW_CONFIG_REL[$i]}"
    session="${ROW_SESSION[$i]}"
    case "$config_rel" in
        /*) config_abs="$config_rel" ;;
        *) config_abs="$PROJECT_ROOT/$config_rel" ;;
    esac

    log "== Row $((i + 1))/$NUM_ROWS: $machine / $config_rel / $session =="

    # --- 1. config exists? ---
    if [ ! -f "$config_abs" ]; then
        log "  ABORTED: config not found: $config_abs"
        ROW_STATUS[$i]="ABORTED (config not found)"
        log ""
        continue
    fi

    # --- 2. parse output / symlink fields ---
    output_name="$(toml_field output "$config_abs")"
    symlink_val="$(toml_field symlink "$config_abs")"

    if [ -z "$output_name" ]; then
        log "  ABORTED: could not parse 'output' field from $config_abs"
        ROW_STATUS[$i]="ABORTED (no output field)"
        log ""
        continue
    fi
    # strip any trailing slash so we build directory paths consistently
    output_name="${output_name%/}"
    ROW_OUTPUT[$i]="$output_name"

    if [ -n "$symlink_val" ]; then
        log "  ABORTED: config sets a non-blank symlink ('$symlink_val') --"
        log "  the documented os.symlink trailing-slash bug means this isn't"
        log "  a currently-supported path (see CLAUDE.md). Blank it first."
        ROW_STATUS[$i]="ABORTED (non-blank symlink)"
        log ""
        continue
    fi

    outdir="$PROTEUS_OUTPUT_DIR/$output_name"

    # --- 3. clobber check ---
    if [ -d "$outdir" ] && [ -n "$(ls -A "$outdir" 2>/dev/null)" ]; then
        if [ "$FORCE_CLOBBER" -eq 1 ]; then
            log "  WARNING: --force-clobber set -- '$outdir' already exists and"
            log "  is non-empty. Grid.__init__ will shutil.rmtree it entirely"
            log "  (no --dry-run protection). Proceeding anyway, as requested."
        else
            log "  SKIPPED: output directory already exists and is non-empty:"
            log "    $outdir"
            log "  proteus grid would unconditionally rmtree it (Grid.__init__,"
            log "  no --dry-run protection) -- refusing. Use --force-clobber to"
            log "  override deliberately."
            ROW_STATUS[$i]="SKIPPED (output exists)"
            log ""
            continue
        fi
    fi

    # --- 4. busy check (skip if this machine already checked/used this run) ---
    if [ -n "${MACHINE_CHECKED_THIS_RUN[$machine]:-}" ]; then
        log "  Busy check skipped: $machine already used earlier in this run"
        log "  (treated as deliberate concurrent use, e.g. a multi-core machine"
        log "  running more than one sweep)."
    else
        ssh_rc=0
        ps_output="$(ssh -o ConnectTimeout="$SSH_TIMEOUT" -o BatchMode=yes "$machine" \
            "uptime; ps aux --sort=-%cpu | grep -Ei 'python|julia|proteus|agni|spider' | grep -v grep" \
            2>&1)" || ssh_rc=$?

        if [ "$ssh_rc" -ne 0 ] && [ -z "$ps_output" ]; then
            log "  SKIPPED: could not reach $machine over SSH"
            ROW_STATUS[$i]="SKIPPED (unreachable)"
            log ""
            continue
        fi

        busy_lines="$(printf '%s\n' "$ps_output" | grep -v '^root ' | grep -Ei 'python|julia|proteus|agni|spider' || true)"
        if [ -n "$busy_lines" ]; then
            log "  SKIPPED: $machine appears busy with a non-root process:"
            printf '%s\n' "$busy_lines" | sed 's/^/    /'
            ROW_STATUS[$i]="SKIPPED (machine busy)"
            log ""
            continue
        fi
        log "  $machine confirmed idle."
    fi
    MACHINE_CHECKED_THIS_RUN[$machine]=1

    # --- 5. launch ---
    if [ "$DRY_RUN" -eq 1 ]; then
        log "  [dry-run] would launch tmux session '$session' on $machine running:"
        log "    nice -n 19 proteus grid -c $config_abs"
        ROW_STATUS[$i]="DRY-RUN (would launch)"
    else
        ssh "$machine" "tmux new-session -d -s '$session' -c $PROTEUS_DIR \
            && tmux send-keys -t '$session' 'module load netcdf/4-2025.01' C-m \
            && sleep 2 \
            && tmux send-keys -t '$session' 'cd PROTEUS' C-m \
            && sleep 1 \
            && tmux send-keys -t '$session' 'conda activate proteus' C-m \
            && sleep 3 \
            && tmux send-keys -t '$session' 'nice -n 19 proteus grid -c $config_abs' C-m"
        log "  Launched tmux session '$session' on $machine."
        ROW_STATUS[$i]="LAUNCHED"
    fi

    log ""

    # --- 6. stagger ---
    is_last_row=$((i == NUM_ROWS - 1))
    if [ "$is_last_row" -eq 0 ] && [ "$DRY_RUN" -eq 0 ] && [ "$STAGGER_SECONDS" -gt 0 ]; then
        log "Sleeping ${STAGGER_SECONDS}s before the next row (NFS-contention stagger)..."
        sleep "$STAGGER_SECONDS"
        log ""
    fi
done

# ---------------------------------------------------------------------------
# Final confirm pass
# ---------------------------------------------------------------------------

if [ "$DRY_RUN" -eq 0 ]; then
    log "=============================================================="
    log "Confirm pass: polling manager.log for launched rows (up to ${CONFIRM_TIMEOUT_SECONDS}s each)"
    log "=============================================================="

    for i in "${!ROW_MACHINE[@]}"; do
        [ "${ROW_STATUS[$i]}" = "LAUNCHED" ] || continue

        machine="${ROW_MACHINE[$i]}"
        output_name="${ROW_OUTPUT[$i]}"
        session="${ROW_SESSION[$i]}"
        manager_log="$PROTEUS_OUTPUT_DIR/$output_name/manager.log"

        log "-- Confirming $session ($output_name) --"
        elapsed=0
        poll_interval=15
        confirmed=0
        while [ "$elapsed" -lt "$CONFIRM_TIMEOUT_SECONDS" ]; do
            if [ -f "$manager_log" ] && grep -q "Starting process manager" "$manager_log" 2>/dev/null \
                && grep -Eq "queued.*running.*exited" "$manager_log" 2>/dev/null; then
                confirmed=1
                break
            fi
            sleep "$poll_interval"
            elapsed=$((elapsed + poll_interval))
        done

        if [ "$confirmed" -eq 1 ]; then
            log "  Confirmed: real progress seen in $manager_log"
            ROW_STATUS[$i]="LAUNCHED (confirmed)"
        else
            log "  UNCERTAIN: no 'Starting process manager' + progress line seen"
            log "  in $manager_log within ${CONFIRM_TIMEOUT_SECONDS}s. This can be a"
            log "  genuine benign NFS-import-contention delay (seen up to ~4 min"
            log "  on 2026-07-27) rather than a real failure -- check manually:"
            log "    ssh $machine \"tmux capture-pane -t $session -p | tail -20\""
            ROW_STATUS[$i]="UNCERTAIN (check manually)"
        fi
        log ""
    done
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

log "=============================================================="
log "Summary"
log "=============================================================="
for i in "${!ROW_MACHINE[@]}"; do
    printf '  %-10s %-45s %-20s %s\n' \
        "${ROW_MACHINE[$i]}" "${ROW_CONFIG_REL[$i]}" "${ROW_SESSION[$i]}" "${ROW_STATUS[$i]}"
done
