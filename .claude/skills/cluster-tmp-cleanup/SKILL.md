---
name: cluster-tmp-cleanup
description: Use when checking or clearing /tmp disk usage on the IoA cluster machines (cap*), or when IT reports /tmp filling up — scripts/cleanup_proteus_tmp.py and its daily cron job. Explains the underlying PROTEUS AGNI temp-directory leak this works around.
---

# Cleaning up leaked /tmp scratch directories: `scripts/cleanup_proteus_tmp.py`

**Why this exists**: IT (Neil) reported on 2026-07-29 that several cap*
machines had run out of `/tmp` space, full of uncleared "proteus"
directories (see `IT_email.txt`). Root cause, confirmed by reading the
PROTEUS source: this project's fiducial config uses `atmos_clim.module =
"agni"`, and AGNI creates a fresh scratch directory for *every single
radiative-transfer call* (i.e. every coupling iteration, of every
grid-sweep case) via `create_tmp_folder()`
(`PROTEUS/src/proteus/utils/helper.py:119`), which falls back to
`/tmp/proteus_<random>/` whenever `$TMPDIR` is unset (always true here,
nothing in this project sets it). `atmos_clim/agni.py` (~line 634) never
removes that directory afterwards — unlike the sibling `janus.py` module,
which does the equivalent thing but calls `shutil.rmtree()` when done
(`janus.py:255-256`). A second, once-per-*case* leak of the same kind
happens at case startup (`utils/coupler.py:1696`,
`dirs['temp'] = create_tmp_folder()`); grepping the rest of the codebase
shows `dirs['temp']` is never read again anywhere, so that directory is
orphaned from the moment it's created. This is a genuine upstream PROTEUS
bug (not something specific to how this project configured/launched
anything) that predates this project's grid sweep — stray directories
dated back to August 2025 were found during investigation — but the
sweep's 14-machine, weeks-long, many-iterations-per-case usage exercises
it far harder than a normal single PROTEUS run ever would. Confirmed live
via SSH on 2026-07-29, before any cleanup: cap001a had 568
`/tmp/proteus_*` dirs (55% of a 16G `/tmp`), cap003d 735 (73% of 16G),
cap004d 905 (37% of 32G), and cap005a (running two concurrent batches, 128
workers) 3852 (78% of 32G).

`cleanup_proteus_tmp.py` is the stopgap fix on our side (deleting what's
already been leaked, daily) while the actual code-level fix — adding the
missing `shutil.rmtree(io_dir)` to `agni.py`, matching `janus.py` —
remains a separate, not-yet-done change to PROTEUS itself.

**Why it's safe to run while batches are live**: a case's real,
checkpointed state (interior snapshots, `runtime_helpfile`, logs, etc.)
always lives under PROTEUS's own output directory on NFS
(`PROTEUS/output/<batch>/case_NNNNNN/` or
`raw_grid_output/<batch>/case_NNNNNN/`), never under `/tmp` — nothing this
script touches can destroy actual simulation progress. The only
theoretical risk is deleting a `/tmp/proteus_*` directory while AGNI is
still actively writing into it mid-iteration; this is guarded against with
an age cutoff (`--min-age-minutes`, default 60 — a single AGNI
radiative-transfer call takes at most a few minutes, so anything older
than an hour is certainly orphaned) and an owner filter (`-user
"$(id -un)"`, so it can never touch another user's files on these shared
machines). Reuses `harvest_completed_cases.py`'s `DEFAULT_MACHINES`/SSH
conventions directly (`import harvest_completed_cases as hc`, see the
`harvest-batches` skill) rather than duplicating the machine list.

```bash
python3 scripts/cleanup_proteus_tmp.py --dry-run   # preview only
python3 scripts/cleanup_proteus_tmp.py               # actually delete
```

**First real run, 2026-07-29**: removed 12,062 orphaned directories across
all 14 reachable machines in one pass (cap005a alone: 3852). Verified
directly afterwards, per-machine, that `/tmp` usage dropped to ~1% on the
machines checked and that every still-live batch's worker processes
(`pgrep -af 'proteus start'`) were unaffected — the machines that showed
zero live workers both before and after had already finished their
batches the day before (2026-07-28, per their own `manager.log`
"GridPROTEUS finished" lines), unrelated to this cleanup.

## Scheduled cleanup via cron

`cleanup_proteus_tmp.py` runs automatically every day at 06:30 (30 minutes
before the 07:00 harvest job — see the `harvest-batches` skill — so the
two don't overlap), via this machine's user crontab — added 2026-07-29,
same crontab as `harvest_completed_cases.py` (see that skill for the
general cron conventions here: absolute conda-env `python3` path,
`flock -n` to prevent overlapping runs, per-day log files under `logs/`,
fallback-only crontab redirect).

```cron
30 6 * * * flock -n /tmp/k218b_cleanup_tmp_cron.lock /data/rdc49-2/anaconda3/envs/proteus/bin/python3 /data/rdc49-2/K218b_project/scripts/cleanup_proteus_tmp.py >> /data/rdc49-2/K218b_project/logs/cleanup_proteus_tmp_cron_fallback.log 2>&1
```

Installed by appending to the existing crontab via `crontab -l` /
`crontab <file>`, confirmed by diff to have added only this one line —
the harvest job and the two long-disabled unrelated jobs were left
untouched. Follow the same procedure (diff before/after) for any future
edit — see the `harvest-batches` skill for why.
