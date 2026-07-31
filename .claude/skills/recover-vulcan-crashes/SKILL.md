---
name: recover-vulcan-crashes
description: Use when simulation_data/ cases are tagged category 7 (`7_crash_atmos_chem_step...` — crashed only in the offline VULCAN post-processing step, main loop already succeeded) and need their VULCAN + petitRADTRANS post-processing re-run in place, across the cluster. Covers scripts/generate_vulcan_rerun_configs.py, scripts/launch_vulcan_reruns.sh, scripts/resume_vulcan_rerun.sh, and scripts/retag_vulcan_reruns.py — the `proteus offchem`/`proteus observe` recipe, cluster orchestration, a set -e/errexit bug that can silently kill a machine's launcher mid-run, and how to tell a real recovery-worthy crash from a genuine unrelated physics/data failure.
---

# Recovering category-7 (VULCAN post-processing crash) cases

**Background**: category 7 (`7_crash_atmos_chem_step[_<signature>]`, see the
`harvest-batches` skill) means the expensive main coupled interior-atmosphere
loop already finished successfully — only the offline VULCAN chemistry
step (and, downstream of it, the petitRADTRANS `observe` step) crashed. A
fresh single-point `proteus grid` config (the `recover-interrupted-grid-points`
skill's approach) would waste that already-completed main-loop compute, so
`generate_gapfill_configs.py` deliberately never regenerates these. This
skill is the correct recovery path instead: re-run **only** the two
post-processing stages, in place, against the existing
`simulation_data/<case>/` directory.

## Two upstream shared-file race conditions (check these are fixed before using this)

Historically (through 2026-07-30) category-7 crashes were caused by VULCAN's
offline chemistry step writing into files shared across every concurrent
case cluster-wide, non-atomically:

1. `chem_funs.py` (VULCAN's compiled reaction-function module) — was
   written to the single shared installed path
   (`VULCAN_DIR/src/vulcan/chem_funs.py`); fixed in the VULCAN submodule
   (commit `45177d3`, "Generate chem_funs.py into a private per-case path,
   not the shared install") to write into each case's own `output_dir`
   instead.
2. The reaction **network definition file** itself (e.g.
   `thermo/SNCHO_photo_network.txt`) — `make_chem_funs.read_network()`
   opens `vulcan_cfg.network` and rewrites it in place (re-numbering
   reactions), and `vcfg.network` pointed at the same shared
   `THERMO_DIR/<network_file>` install path. This is a *second*, distinct
   race from (1) and was **not** covered by the chem_funs.py fix — found
   2026-07-30 when the shared `SNCHO_photo_network.txt` was discovered
   truncated to 0 bytes (restorable via `git restore` in the VULCAN repo,
   since it's a tracked file). Fixed in
   `src/proteus/atmos_chem/vulcan.py`: the network file is now copied into
   the case's own `dirs['output/offchem']` directory before `vcfg.network`
   is set, so `read_network()`'s rewrite only ever touches a private copy.

**Before relying on this recipe to actually fix anything**, confirm both
fixes are present (`git log -1` in the VULCAN submodule for (1); `grep
"vcfg.network = os.path.join(dirs" src/proteus/atmos_chem/vulcan.py` for
(2)) — otherwise you'll just reproduce the original crash storm.

## The recipe: `proteus offchem` + `proteus observe`

Both are standalone PROTEUS CLI subcommands (`proteus_CLAUDE.md`'s entry
points list) that read the last row of the case's existing
`runtime_helpfile.csv` and run exactly one stage — no main-loop
re-execution, no checkpoint logic:

```
proteus offchem -c <config.toml>
proteus observe  -c <config.toml>    # must run after offchem; reads its output
```

To point these at an already-harvested `simulation_data/` case rather than
a fresh `PROTEUS/output/` directory: copy the case's own `init_coupler.toml`
(sitting in the case directory — the frozen config PROTEUS wrote at launch)
and override **only** `[params.out].path` to the case's own **absolute**
path. `Proteus`'s directory resolution does `os.path.join(proteus_root,
'output', params.out.path)`, and `os.path.join` discards everything before
an absolute-path component, so this makes both commands operate on the
existing case directory directly — no copying/symlinking of case data
needed. Use `tomlkit` (already in the `proteus` conda env) to parse/edit/
write back, preserving comments/formatting — there's no existing read-write
TOML helper in this project (`harvest_completed_cases.load_toml()` is
read-only `tomllib`).

## The scripts

- **`scripts/generate_vulcan_rerun_configs.py`** — finds every
  `simulation_data/` directory tagged category 7, writes an edited
  per-case config (as above) into a fresh timestamped output directory
  under `grid_sweep_configs/vulcan_rerun_configs/`, partitioned
  round-robin into one subdirectory per target machine. Also writes one
  `launch_<machine>.sh` per machine (bounded-concurrency bash loop,
  `MAXJOBS` concurrent case-pipelines, each running `offchem && observe`
  sequentially for one case) and a `manifest.csv`
  (`machine,launch_script_path,session_name`). Nothing is launched
  automatically. `--limit N` for a small pilot; `--machines`/`--max-jobs`
  to control placement and concurrency; `--report-only` to just see the
  category-7 count.

- **`scripts/launch_vulcan_reruns.sh <manifest.csv>`** — SSH orchestrator
  adapted from `launch_batches.sh`'s busy-check/tmux-launch/stagger
  pattern (see the `launch-grid-batch` skill), but for launch scripts
  instead of `proteus grid` configs — no output-directory clobber-check
  (not applicable; we're pointing at existing `simulation_data/`, not
  creating a fresh `Grid` output dir). `--dry-run` first. **Always
  re-check live cluster occupancy before choosing target machines** —
  this project routinely has several of the 14 known machines busy with
  planned grid-sweep batches (see `harvest-batches`/`launch-grid-batch`)
  that can run for 3+ days, so the "idle" set drifts constantly.

- **`scripts/resume_vulcan_rerun.sh <machine_dir> [max_jobs]`** — resumes
  a machine's rerun directory, skipping any `grid_*.toml` that already has
  a `.rerun.log` (already attempted, success or fail). Idempotent, safe to
  run repeatedly. This is the recovery tool for the `set -e` bug below —
  send it into the machine's existing tmux session via `tmux send-keys`
  rather than starting a new session.

- **`scripts/retag_vulcan_reruns.py <rerun_configs_dir> [--apply]`** —
  independently verifies each case's outcome **from disk state**
  (`offchem/vulcan.csv` non-empty AND `observe/` non-empty), not from
  `rerun_results.log`, so it's correct even given the logging bug below.
  On success, renames the case directory's outcome-tag suffix to
  `1_success_recovered_vulcan_rerun` (preserving the
  `grid_H..._C..._N..._S..._fO2..__from_<batch>_<case>__` prefix). On
  failure, leaves the tag unchanged (`7_crash_atmos_chem_step...`) so the
  case stays visibly eligible for a future retry. Defaults to dry-run;
  `--apply` to actually rename. Safe/cheap to re-run at any time —
  prefer running it once as a final pass after a whole rerun batch
  completes, rather than repeatedly mid-flight.

## A `set -e`/`wait -n` bug in launch scripts generated before 2026-07-31

The original `LAUNCH_SCRIPT_TEMPLATE` had `set -e` active, and each
case's `(offchem && observe) > log 2>&1` command sat as a bare statement
followed by a separate `if [ $? -eq 0 ]; then ...` check — **not** wrapped
directly in the `if` condition. Under `errexit`, a failing case's command
group terminates its enclosing subshell immediately, before the `if`/echo
logic ever runs, so:
1. The per-case `FAILED` line never gets written to `rerun_results.log`
   (the case's own `.rerun.log` file *does* still capture the full
   traceback, since that redirect happens first).
2. Worse: when the main loop's `while ... do wait -n; done` reaps that
   crashed subshell, `wait -n` itself returns non-zero, and **that**
   propagates `errexit` into the *main script* — silently killing the
   entire launcher and abandoning every not-yet-dispatched config on that
   machine.

Fixed in the current template (the failing command is now the direct
condition of the `if`, so `errexit` doesn't fire: `if ( ... ) > log 2>&1;
then ... else ... fi`). Freshly generated configs are safe. If you're
resuming/monitoring a run launched before this fix landed, or just want to
be defensive:

- **`rerun_results.log`'s `SUCCESS` lines are always reliable** (that path
  never hits the bug). Its `FAILED` count is not — track real failures via
  `grep -l Traceback <machine_dir>/*.rerun.log | wc -l` instead.
- **Detecting a dead launcher**: active process count
  (`ps aux | grep -E 'proteus (offchem|observe)' | grep -v grep | wc -l`)
  sitting well below `MAXJOBS` with `never_attempted > 0` remaining
  (`ls <dir>/grid_*.toml | wc -l` minus `ls <dir>/*.rerun.log | wc -l`) is
  the signal — confirm via `tmux capture-pane -t vulcan_rerun_<machine> -p
  -S -10`: a returned shell prompt with no further activity after the last
  `"Re-running..."`/`"Resuming..."` line means the launcher died. (A
  machine legitimately going idle with `never_attempted == 0` — it simply
  finished its whole allocation — is normal completion, not a death.)
- **Recovery**: `ssh <machine> "tmux send-keys -t vulcan_rerun_<machine>
  'bash scripts/resume_vulcan_rerun.sh <machine_dir> <max_jobs>' C-m"` — do
  **not** start a fresh `tmux new-session`, the old one's shell is still
  alive and idle, just send the command into it.

In practice (the 2026-07-30/31 recovery of 292 category-7 cases across 7
machines) this hit 6 of 7 machines at least once over ~14 hours — expect
it to recur somewhat regularly on any pre-fix launch script, and check for
it on every monitoring pass, not just once.

## Sizing concurrency

petitRADTRANS's `observe` stage appears NFS/I/O-bound rather than
CPU-bound when several run concurrently on one machine (processes sit in
`D` state at low CPU%, each individual run taking up to ~3x longer than
solo) — likely opacity-table reads from shared storage. It's still a net
throughput win (measured ~1.8x wall-clock speedup at 8-way concurrency vs.
serial, not the naive 8x), but pushing `MAXJOBS` up doesn't scale linearly
and cluster-wide contention (many machines at once) is untested beyond
what's been run so far. `MAXJOBS=6-8` per machine is a reasonable default;
watch `free -h` and process states rather than assuming higher is better.
Each case's full `offchem`+`observe` pipeline has taken on the order of
1-3 hours end-to-end in practice — size expectations (and monitoring
cadence) accordingly, not against the main loop's own runtime.

## Genuine (non-race) failure signatures seen — don't expect this recipe to fix these

A rerun won't succeed for every category-7 case — some crashed for reasons
unrelated to either shared-file race, and will keep failing identically on
retry (correctly left tagged by `retag_vulcan_reruns.py` for manual
follow-up, not silently misclassified as recovered):

- `Could not find NetCDF file '.../data/<n>_atm.nc'` →
  `TypeError: 'NoneType' object is not subscriptable` — the case's `data/`
  directory is missing the specific timestep snapshot `offchem` wants.
  Seen repeatedly on `batch10` cases specifically — may indicate a
  batch-wide data-completeness gap worth a separate look.
- `ValueError: array must not contain infs or NaNs` (in
  `numpy.asarray_chkfinite`) — a genuine VULCAN numerics failure for that
  case's composition; also listed as a known rare signature in the
  `harvest-batches` skill's `KNOWN_FAILURE_SIGNATURES`, i.e. this predates
  the race-condition bug entirely.
- `ValueError: Requested wavelength interval (...) is out of opacities
  table wavelength grid (...)` — a petitRADTRANS config/opacity-table
  mismatch in the `observe` stage (so `offchem` succeeded but `observe`
  didn't); a data/config issue, not something a retry fixes.

## After a rerun batch finishes

1. `python3 scripts/retag_vulcan_reruns.py <rerun_configs_dir>` (dry-run),
   review, then `--apply`.
2. Rebuild `simulation_data/grid_master.csv` and its `chisq_*` columns —
   see the `grid-summary-and-chisq` skill (`build_grid_summary.py` then
   `compute_spectral_chisq.py`, in that order — the run-order gotcha there
   applies here too, since retagging changed a batch of `source_outcome`
   values).
