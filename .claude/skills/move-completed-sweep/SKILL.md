---
name: move-completed-sweep
description: Use when a whole PROTEUS grid-sweep batch has finished (every case terminal) and needs moving from raw_grid_output/ (or PROTEUS/output/) into simulation_data/. Covers scripts/move_sweep.py and the equivalent manual steps. Not for partially-finished batches — see the harvest-batches skill for that.
---

# Moving a completed sweep into `simulation_data/`

A sweep directory — whether the real one under `raw_grid_output/<name>/`
or an older one directly under `PROTEUS/output/<name>/` — contains one
subdirectory per grid point, `case_000000/`, `case_000001/`, etc., each
with:

- `status` — two-line run state: a numeric code, then a text label, e.g.
  `Running`, `Completed (solidified)`, `Completed (net flux is small)`, or
  `Error (...)`. **Check this in every case before moving the sweep** — a
  folder isn't ready to move while any case still says `Running` (still in
  progress) or has no `status` file at all (never started/crashed early).
  `Error (...)` cases are not failures of the move itself, but note them so
  the analysis in `plotting_scripts/` knows which grid points to
  exclude/flag.
- `init_coupler.toml` — the resolved config for that case, including its
  grid-point parameter values (e.g. `fO2_shift_IW`) — this is how
  `plotting_scripts/` should recover each case's position in the fO2 x H x C
  x N x S grid.
- `runtime_helpfile.csv`, `proteus_00.log`, `agni_recent.log` — run history
  and logs.
- `data/` — interior/atmosphere snapshots.
- `observe/`, `offchem/` — the atmospheric chemistry and synthetic spectrum
  output generated automatically by the pipeline.
- `plots/` — PROTEUS's own built-in diagnostic plots for that case.

**Preferred method**: `scripts/move_sweep.py <sweep_name>` prints the
per-case status tally (parsing the two-line `status` file format correctly),
warns on `Running`/missing-status cases without hard-blocking, shows the
total size, and prompts for confirmation before moving (`--yes` to skip the
prompt):

```bash
python3 scripts/move_sweep.py <sweep_name>
```

It looks for the real data under `raw_grid_output/<sweep_name>/` first
(the symlinked-batch case); if not found there, it falls back to
`/data/rdc49-2/PROTEUS/output/<sweep_name>/` (the older, non-symlinked
case — see the `launch-grid-batch` skill for why the symlink scheme is
currently disabled). Either way it moves the *real* directory — never the
symlink itself — into `simulation_data/`, and if a matching symlink was
left behind under `PROTEUS/output/<sweep_name>` pointing at the directory
just moved, it deletes that now-dangling symlink too.

Equivalent manual steps, if not using the script: tally status labels
across a sweep (adjust the path if it's the older non-symlinked case) with

```bash
for f in raw_grid_output/<sweep_name>/*/status; do tail -n1 "$f"; echo; done | sort | uniq -c
```

then, once satisfied, move the whole sweep folder in one go (same
filesystem, so `mv` is a cheap rename, not a copy) and clean up the
dangling symlink:

```bash
mv raw_grid_output/<sweep_name> simulation_data/
rm /data/rdc49-2/PROTEUS/output/<sweep_name>   # only if it's a symlink pointing at the folder just moved
```

**Do not `mv` or `cp` the `PROTEUS/output/<sweep_name>` path itself when
it's a symlink** — that relocates the symlink, not the data, and leaves the
real directory orphaned under `raw_grid_output/`.

Either way, this preserves the full `case_NNNNNN/` structure, which is what
`plotting_scripts/` should expect to read. Because `simulation_data/*` and
`raw_grid_output/*` are both gitignored (see `.gitignore`), none of this
has any effect on git history — it will not show up in `git status`.
