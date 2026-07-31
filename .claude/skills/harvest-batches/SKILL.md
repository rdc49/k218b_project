---
name: harvest-batches
description: Use when checking on or pulling finished cases out of grid-sweep batches that are still partway through running (scripts/harvest_completed_cases.py) — status summaries, the log-cross-checking/SSH-liveness design, harvested-case naming, outcome categories 1-7, and the daily cron job that runs this automatically. Not for a whole already-finished batch — see move-completed-sweep for that.
---

# Monitoring and harvesting while batches are still running: `scripts/harvest_completed_cases.py`

`move_sweep.py` (see the `move-completed-sweep` skill) moves one *entire*
sweep folder at once, once every case in it is done — the right tool once
a batch has fully finished. `scripts/harvest_completed_cases.py` is for
the more common situation of several batches running concurrently on
different cluster machines, each partway through its cases: it (1) prints
a per-batch status summary, discovered dynamically from
`grid_sweep_configs/batch_configs/batch*.toml` (no hardcoded batch list to
keep in sync), then (2) moves every individual case with a genuinely
terminal outcome straight into `simulation_data/`, leaving still-running
cases in their batch untouched. Safe to re-run repeatedly (by hand, cron,
or its own `--watch SECONDS` loop) — already-harvested cases are simply
gone from the source next time, and moved-to destinations are never
overwritten.

**Does not trust the on-disk `status` file at face value.** It reuses the
log-cross-checking approach from `scripts/analyze_grid_sweep.py` (a
separate, pre-existing tool, not written by this assistant): for every
case with a `proteus_00.log`, the real outcome is derived from the log
itself — a `Traceback` means a crash (tagged with the known-failure
signature from `analyze_grid_sweep`'s table when recognised, e.g.
`atmodeller_overflow`, `agni_coupling_deadlock`), `"Simulation stopped"`
with no traceback means genuine success, and otherwise the case's
liveness is checked directly rather than trusting `status`. This matters
because `status` can lie — most importantly, a case can still be
genuinely running while its `status` file already claims `"Completed"`;
harvesting that case would rip its directory out from under a live
simulation. Every run also prints a "STATUS-FILE DISAGREEMENTS" section
flagging any case where the on-disk `status` doesn't match the log-derived
outcome, whether or not that case got harvested.

**Liveness is checked via real SSH process queries against the actual
cluster machines, not a local `pgrep` or log-mtime guess** (see below for
why this replaced an earlier, broken design). Once per run (not once per
case), it runs `pgrep -af 'proteus start'` over SSH against every machine
in `DEFAULT_MACHINES` (the 14 known cluster machines, matching
`grid_sweep_cluster_howto.md` — override with `--machines` for a faster,
scoped check, e.g. `--machines cap001a,cap001b`), and a case counts as
alive if any returned line mentions both its batch's `output` name and its
case number (matching PROTEUS's own `<output>/cfgs/<case>.toml`
config-path convention — needed because every batch restarts case
numbering from 0, so a bare case-number match could hit a different
batch's process). A short log-mtime-freshness grace period
(`FALLBACK_ALIVE_LOG_FRESHNESS_SECS`, 5 minutes) is only a fallback, used
when a machine can't be reached over SSH — if any can't be reached, a
warning names them so you know which cases' "dead" classifications that
run are less certain.

```bash
python3 scripts/harvest_completed_cases.py                # summary + harvest
python3 scripts/harvest_completed_cases.py --summary-only  # just the summary
python3 scripts/harvest_completed_cases.py --dry-run        # preview harvesting
python3 scripts/harvest_completed_cases.py --watch 300       # repeat every 5 min
python3 scripts/harvest_completed_cases.py --machines cap001a,cap001b  # scope the SSH check
```

## Aliveness-check bug found during the first real test (2026-07-22 to 2026-07-24)

The very first real test of this script (batches 01/02 launched on
`cap001a`/`cap001b`) surfaced a genuine, confirmed bug in its original
liveness check, not a hypothetical concern: that check matched a bare
`case_NNNNNN` against `pgrep -af 'proteus start'` run **locally** (i.e. on
whichever machine invokes the script — here, the cron host, `calx034`,
which is not one of the 14 cluster machines), falling back to a 15-minute
log-mtime-staleness heuristic (`analyze_grid_sweep.STALE_LOG_SECS`,
inherited unmodified) if that found nothing. Since the script never runs on
the same host as the actual simulations, the `pgrep` signal never once
fired — the 15-minute heuristic was doing 100% of the "is it dead" work,
silently.

That heuristic was too aggressive: the final observe/petitRADTRANS
synthetic-spectrum step can legitimately run for hours without writing a
new line to `proteus_00.log`. Over the two-day test, checked directly by
comparing each harvested case's log/status timestamps against when it was
actually harvested: **14 of 90 harvested cases (~16%) were still genuinely
alive when harvested** — their logs kept growing for 48 minutes to 2.6
hours *after* being moved. At least one (`case_000027`) subsequently
crashed with `FileNotFoundError: No stellar spectrum files ... found in
'.../data'` — its own data directory had been pulled out from under it
mid-run by the premature harvest. (Every `1_success` and every crash-typed
harvest was independently verified as genuinely correct — the bug was
isolated to the `6_unclassified` "killed externally" category.)

Fixed by replacing the liveness check entirely with real SSH process
queries against the actual cluster machines (described above), gathered
once per run rather than once per case. The cron job was paused
(commented out, not deleted — see below) the moment this was found, and
only re-enabled once this fix was tested against the live batch01/batch02
processes and confirmed to match a direct `ps` check on both machines
exactly.

## Renaming and outcome categories

A harvested case is renamed from its batch-local `case_NNNNNN` to
`grid_H<i>_C<i>_N<i>_S<i>_fO2<i>__from_<batch>_<case>__<outcome>`, where
each index (0-3) is that axis's position in `full_parameter_sweep.toml`'s
value list (read from the case's own `init_coupler.toml`, not recomputed
from batch/case numbering, so it's correct even if a batch's internal
split logic ever changes again), and `<outcome>` is the log-derived
category (`1_success`, `2_fatal_crash_main_loop`, `3_crash_observe_step`,
`7_crash_atmos_chem_step` — crashed in the offline VULCAN
post-processing step, after the main loop already finished; see below —
or `6_unclassified` for killed-externally), plus the matched failure
signature name when there is one, e.g.
`grid_H0_C2_N1_S3_fO20__from_batch01_case000042__2_fatal_crash_main_loop_atmodeller_overflow`.
The `grid_H.._C.._N.._S.._fO2..` prefix is what identifies the case's
position in the full 1024-point sweep; the rest is outcome/provenance,
kept for traceability and so failed vs. successful cases can be told apart
(e.g. via `ls simulation_data | grep 1_success`) without reopening each
case's log.

**What the `<i>` indices actually mean**: `simulation_data/GRID_INDEX_LEGEND.txt`
maps every axis's indices (0-3) to their real physical value, e.g. `H0 =
10000.0`, `C1 = 0.032`, `fO23 = 0.0` — written/refreshed by
`write_grid_index_legend()` on every single invocation (summary-only,
dry-run, and real-harvest alike), straight from
`grid_sweep_configs/full_parameter_sweep.toml`, so it can never drift out
of sync with the actual sweep definition. It's the one file inside
`simulation_data/` that's tracked in git despite the rest of that directory
being gitignored (`!simulation_data/GRID_INDEX_LEGEND.txt` in `.gitignore`)
— small, useful documentation, not harvested data.

**Categories 4 and 5 (genuinely still running, whether or not `status`
agrees) are never harvested, and neither is category 6 with no
`proteus_00.log` at all** (ambiguous between never-started and
crashed-before-logging — left alone rather than guessed at).

**Category 7 (`7_crash_atmos_chem_step`): crashed only in the offline
VULCAN post-processing step, main loop already succeeded.** Added
2026-07-28 after discovering that a large fraction of what looked like
`2_fatal_crash_main_loop` cases were actually this — see
`vulcan_chem_funs_race_condition.md` for the full mechanism (a shared,
non-atomically-written `chem_funs.py` inside the PROTEUS/VULCAN install,
raced by every concurrently-running grid point cluster-wide). Distinguished
from categories 2/3 the same way 2-vs-3 already was: `analyze_case()`
(and `harvest_completed_cases.determine_real_outcome()`, via the shared
`classify_crash_stage()` helper both now call) checks whether the literal
log line `"Running atmospheric chemistry"` appears before the crash's
traceback (and `"Observing the planet"` does not) — a positional text
check, not a traceback-file-path check, consistent with how category 3
is detected. Two known signatures are recognised
(`vulcan_chem_funs_race_syntax_error`, `vulcan_chem_funs_race_missing_symjac`,
covering 227/240 of the cases found so far); anything else that crashes
in this same window still gets the bare `7_crash_atmos_chem_step` tag.

**These cases are salvageable but `generate_gapfill_configs.py` will
never regenerate them, under any flag** (see the `recover-interrupted-grid-points`
skill and that script's own docstring) — unlike genuine `crashed` points,
blindly regenerating a fresh single-point config would re-run the whole
case from scratch, wasting the already-completed main-loop compute. The
correct recovery (re-running *only* the post-processing step against the
existing completed state) isn't a script yet — it's future work, blocked
on actually fixing the underlying race condition first. Until then these
points just sit tagged and visible
(`ls simulation_data | grep 7_crash_atmos_chem_step`), not silently lost
track of.

**`scripts/reclassify_vulcan_crashes.py`**: a one-time migration script,
run once on 2026-07-28, that retroactively renamed 240 already-harvested
directories from the bare `2_fatal_crash_main_loop` tag to the new
category 7 tag (the other 51 bare-tagged directories were confirmed
genuinely category 2 and left alone). Re-derives each candidate's real
category from its own log via the same shared `classify_crash_stage()`
helper — not a hardcoded list — so it's safe to re-run if any further
mis-tagged directories are ever found; defaults to dry-run, `--apply` to
actually rename. Not needed going forward for newly-harvested cases —
`harvest_completed_cases.py`/`analyze_grid_sweep.py` now tag category 7
correctly the first time.

## Known limitations

**`proteus grid`'s own manager does one final pass over every case's
status file** right when a whole batch's last case finishes (end of
`Grid.run()` in `PROTEUS/src/proteus/grid/manage.py`), and raises if a
status file is missing. If this script harvests a case out of a batch in
the same instant that batch's very last case completes, that final pass
could crash with a traceback — but only after all of that batch's actual
simulation work is already done, so nothing is lost; the manager process's
log just ends in an exception instead of a clean finish. Narrow window,
cosmetic impact, not otherwise guarded against.

**A batch's own container directory isn't cleaned up.** Once every case in
a batch has been harvested, `raw_grid_output/<batch_output_name>/` still
exists (now containing only `cfgs/`, `logs/`, `manager.log`, etc., no more
`case_*` dirs) — harmless, but a manual `rm -r` once you're sure a batch is
fully drained is fine if you want to tidy it up.

## Scheduled harvesting via cron

`harvest_completed_cases.py` runs automatically every day at 07:00 (all
7 days), via this machine's user crontab (`crontab -l` to view,
`crontab -e` to edit). **It was paused (line commented out, not deleted)
from 2026-07-24 while the aliveness-check bug above was found and fixed,
then re-enabled the same day once the fix was verified against the live
batch01/batch02 processes.** If you ever need to pause it again (e.g.
while changing this script), comment the line out with a `#` prefix plus a
dated reason rather than deleting it, so it's easy to tell it was
deliberate and easy to restore — and always re-check the other lines of
`crontab -l` against what they were before, to make sure the edit didn't
touch unrelated entries (see below).

```cron
0 7 * * * flock -n /tmp/k218b_harvest_cron.lock /data/rdc49-2/anaconda3/envs/proteus/bin/python3 /data/rdc49-2/K218b_project/scripts/harvest_completed_cases.py >> /data/rdc49-2/K218b_project/logs/harvest_cron_fallback.log 2>&1
```

Notes on the specific pieces of this line, for anyone editing it later:

- Uses the `proteus` conda env's own `python3` explicitly by absolute path
  (`/data/rdc49-2/anaconda3/envs/proteus/bin/python3`), not a bare
  `python3` — cron runs jobs in a minimal non-interactive, non-login shell
  that never sources `~/.bashrc` (its `[ -z "$PS1" ] && return` guard skips
  everything for non-interactive shells; see the shell-context gotcha in
  `grid_sweep_cluster_howto.md`), so neither `module load` nor `conda
  activate` ever happens for a cron job on this machine.
- `flock -n /tmp/k218b_harvest_cron.lock` prevents two overlapping runs
  (e.g. if a previous invocation were still going, perhaps stuck on an NFS
  hiccup) — `-n` makes it skip rather than queue if the lock is already
  held, so a stuck run blocks at most that one day's harvest, not every
  subsequent day's.
- The crontab's own `>>` redirect (`logs/harvest_cron_fallback.log`) is
  only a fallback now, catching a catastrophic failure before the script's
  own logging is even set up (e.g. an `ImportError`/`SyntaxError` at
  startup) — it should normally stay empty or near-empty. The real,
  day-to-day log is per-day: the script itself opens
  `logs/harvest_YYYY-MM-DD.log` (see `--log-dir`/`--no-file-log`) and tees
  its own stdout/stderr to both that file and its normal output, so each
  morning's run gets its own file (`harvest_2026-07-24.log`, etc.) instead
  of everything appending to one ever-growing file. Both are gitignored,
  like `simulation_data/` and `raw_grid_output/`. Check the day's file for
  history/errors, not system mail. The script prints timestamped
  start/finish/failure banners into it (see its docstring) precisely so an
  error is easy to find and grep for.
- **Before editing the crontab, always run `crontab -l` first and diff
  after** — this machine's crontab has several independent entries (two
  unrelated backup jobs, disabled 2026-07-24 at the user's request but
  kept commented rather than deleted; this harvest job; and the
  `cluster-tmp-cleanup` skill's cleanup job), and edits should only ever
  add/modify the one line intended, never regenerate the file from
  scratch.

**Unrelated finding worth flagging** (same issue already noted for
`~/.bashrc` in `grid_sweep_cluster_howto.md`): the crontab has a
`GITHUB_TOKEN=...` line with a real personal access token in plaintext.
Don't propagate it anywhere; flag it to the user for rotation if noticed
again.
