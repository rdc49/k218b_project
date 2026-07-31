---
name: launch-grid-batch
description: Use when launching a new proteus grid batch (or a single proteus start run) on the IoA cluster — batch sizing rationale, the mandatory fiducial-config drift check, nice priority, detached-launch conventions, and the raw_grid_output/ symlink bug (currently disabled). See scripts/launch_batches.sh and grid_sweep_cluster_howto.md for the step-by-step mechanics and choosing a machine.
---

# Launching a grid-sweep batch or single PROTEUS run

## Before every launch: diff the fiducial config

**`k218b_fiducial.toml` (project root) is not what `proteus grid` actually
reads.** `grid_sweep_configs/*.toml` files set `ref_config =
"input/nogit_k218b_project/k218b_fiducial.toml"`, a path resolved relative
to the PROTEUS install root (`/data/rdc49-2/PROTEUS/`), i.e. a *separate
copy* living inside the live PROTEUS working directory, not the project's
own file. These two copies can and do drift: as of 2026-07-22 the
PROTEUS-side copy still had the pre-review bugs (stale `mass_tot`
citation, the dead `[star.dummy]` Teff block, `solubility_H2O =
"H2O_basalt_dixon95"`, the `outgas.atmodeller.eos_* = "none"` overrides)
that were already fixed in the project's copy. **Before launching any
grid/batch run, diff `k218b_fiducial.toml` against
`/data/rdc49-2/PROTEUS/input/nogit_k218b_project/k218b_fiducial.toml` and
copy the project's version over if they differ** — otherwise fixes made in
the project repo silently never reach the actual simulations. Same applies
to any other fiducial variant referenced by a grid config's `ref_config`
(e.g. a `calliope`-backend equivalent).

## `nice` and detached launches

**Always launch `proteus grid` as `nice -n 19 proteus grid -c <config>`,
never bare `proteus grid`.** The IoA cluster machines this project's
batches run on are shared with other people, not dedicated to this
project; `nice -n 19` (lowest scheduling priority) makes a sweep's
CPU-bound worker subprocesses yield to anyone else's work on the same node
rather than compete with it on equal footing. The workers are forked from
the niced parent process and inherit its priority automatically, so
niceing just the `proteus grid` invocation itself is enough — no need to
nice each case individually. This is a courtesy floor, not a substitute
for checking a machine's load before picking it (see
`grid_sweep_cluster_howto.md`'s "Choosing a machine" section).

Long grid runs (or a single `proteus start`) should be launched detached,
e.g.:

```bash
nohup proteus start -c <cfg.toml> --offline > output/<run>/launch.log 2>&1 & disown
```

Never foreground a multi-hour run. Use `-r`/`--resume` to continue an
interrupted single run, and `--deterministic` if numerically fragile runs
need bit-reproducibility. (Note `proteus grid` itself has no equivalent
resume mechanism for individual killed cases — see the
`recover-interrupted-grid-points` skill.)

## Batch sizing rationale

`grid_sweep_configs/batch_configs/batch01.toml`..`batch16.toml` split the
full 1024-point grid (`full_parameter_sweep.toml`) into 16 batches of
64 points each (split by `fO2_shift_IW`, 4 values, x `H_budget` low/high
half, 2 groups, x `C_budget` low/high half, 2 groups; each batch keeps the
full N/S axes).

**Batch size trades off two competing cluster-aware goals, not an
arbitrary number.** `proteus grid` clamps its concurrency to
`min(max_jobs, batch_size, os.cpu_count())`
(`PROTEUS/src/proteus/grid/manage.py:367-370`) — `max_jobs=500` in every
batch file is already effectively unlimited, so batch *size* (grid-point
count) is what determines whether a machine's cores sit idle. A batch
smaller than the launching machine's core count leaves the difference
permanently idle for the whole run; an oversized batch never wastes
cores — it just runs more internal waves and takes proportionally longer
on a smaller machine. Given that, there are two things to optimise for on
the IoA cluster (14 machines, 24-112 cores each; `cap005a` is the outlier
at 112, the other 13 are 24 or 48 — see `grid_sweep_cluster_howto.md`):
(1) no single batch should idle cores on whichever machine runs it, and
(2) there should be enough independent batches to occupy most/all
14 machines at once. These pull in opposite directions given the fixed
1024-point grid: an earlier version sized every batch at 128 (above
`cap005a`'s 112 cores), satisfying (1) perfectly but only yielding
8 batches total — capping simultaneous cluster usage at 8 of 14 machines
no matter how many were actually free. The current 64-point size is
instead sized above the *typical* machine (48 cores, covering 13 of the
14 machines), giving 16 batches — enough for all 14 machines with
2 spare. The accepted trade-off: a single 64-point batch landing on
`cap005a` only fills 64 of its 112 cores, leaving 48 idle for that batch's
duration — a much smaller loss than leaving whole machines idle, so the
preferred one here. If `cap005a`'s spare capacity matters for a given
push, launch a second batch there concurrently in a separate tmux session
rather than leaving it under-filled. Don't shrink batch size below 48 (the
typical machine's core count) without re-checking this reasoning, and
don't grow it back toward 112+ without remembering that re-caps
simultaneous machine usage.

The batches exactly partition the full grid — every batch's `output`
folder name is unique and the union of all 16 batches' grid points equals
the full grid with zero overlap (verified programmatically when the
batches were created/re-split). If the full sweep definition, the batch
size, or the cluster's machine specs ever change, regenerate the batch
files from scratch rather than hand-editing them independently, to keep
the partition exact and the sizing reasoning valid.

## Where sweep output actually lands: `raw_grid_output/` (currently disabled — see bug below)

**Current reality (as of 2026-07-22): every batch config's `symlink` field
is blank, and every sweep — including `batch01`/`batch02`, first launched
on `cap001a`/`cap001b` this date — writes directly under
`/data/rdc49-2/PROTEUS/output/<name>/`, the pre-`raw_grid_output/` layout.**
`scripts/move_sweep.py` and `scripts/harvest_completed_cases.py` both
already handle this layout correctly (it's their fallback path, exercised
automatically whenever a batch's `symlink` is blank) — nothing extra is
needed to work with sweeps launched this way.

**The original design** (kept here so it can be re-enabled once fixed):
set `symlink` to an absolute path under `raw_grid_output/` in this project
(e.g.
`/data/rdc49-2/K218b_project/raw_grid_output/k218b_project_main_parameter_sweep_batch01`).
PROTEUS's grid runner (`Grid.__init__` in
`PROTEUS/src/proteus/grid/manage.py:100-158`) always anchors a sweep's name
at `PROTEUS/output/<name>/`, but when `symlink` is set it's supposed to
create the *real* directory at that path instead and leave only a symlink
at `PROTEUS/output/<name>/` pointing to it, so the actual data would be
written directly into this project's `raw_grid_output/` — on the same
NFS-shared filesystem (`/data/rdc49-2/`) from any of the 14 cluster
machines — with `PROTEUS/output/<name>` acting as nothing more than a
pointer to it.

**Why it's disabled: a real PROTEUS bug, confirmed via an actual launch
attempt, not a config mistake.** `Grid.__init__` always constructs
`self.outdir` with a trailing slash (`PROTEUS_DIR + '/output/' + name +
'/'`, manage.py:109/119) and passes it straight to `os.symlink(symlink_dir,
self.outdir)` at line 158. `os.symlink()` rejects a link path with a
trailing slash whenever the link doesn't already exist
(`FileNotFoundError`), which is always true the first time a sweep is
launched. Reproduced in isolation with a plain Python script, and via a
real `proteus grid -c ... --dry-run` attempt on `cap001a` on 2026-07-22,
both failing identically. This affects *any* config that sets `symlink`,
on any machine — not specific to a particular batch. The workaround used
for the first real launch was a scratch copy of the batch config with
`symlink = ""` under `PROTEUS/input/nogit_grid_launch_configs/` (per the
"avoid clobbering" pre-flight procedure in `grid_sweep_cluster_howto.md`);
the checked-in batch configs were then updated to match (`symlink = ""`
everywhere) so `harvest_completed_cases.py`'s default discovery — which
reads the checked-in configs, not any scratch copy — correctly finds where
the data really is.

**To re-enable `raw_grid_output/` once the upstream bug is fixed**: patch
`Grid.__init__` to strip the trailing slash before the `os.symlink` call
(e.g. `os.symlink(self.symlink_dir, self.outdir.rstrip('/'))`), verify with
a throwaway config, then set `symlink` back to the intended
`raw_grid_output/<output_name>` path in each batch config. Nothing else
needs to change — `move_sweep.py` and `harvest_completed_cases.py` already
support both layouts and pick the right one automatically per batch.

**Caveat regardless of which layout is active**: this only concerns sweeps
launched via the batch configs in this project. Older sweeps launched
before either scheme existed also write directly under
`/data/rdc49-2/PROTEUS/output/<name>/` with no symlink involved —
handled by the same fallback path.

## Automating a multi-machine launch: `scripts/launch_batches.sh`

Automates the manual "Steps to launch a sweep" procedure in
`grid_sweep_cluster_howto.md` across a manifest of machine/config/session
rows: live busy-check, refuse-by-default clobber check, staggered
detached-tmux launch, then a confirm pass. Run
`scripts/launch_batches.sh --help` for full usage; see the safety note in
its own header on why the clobber check exists — `proteus grid`'s
`Grid.__init__` unconditionally wipes an existing output directory with
no `--dry-run` protection.
