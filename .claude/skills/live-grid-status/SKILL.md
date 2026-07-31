---
name: live-grid-status
description: Use when the user wants to know what's currently running on the cluster right now -- "what's running", "how long have these cases been going", "live status of the grid sweep", "show me the currently-running simulations" -- as opposed to the harvest log's daily summary. Covers scripts/generate_grid_status.py and publishing its output as a Claude Artifact.
---

# Live grid-sweep status report

`scripts/harvest_completed_cases.py` (see the `harvest-batches` skill) tells
you what finished since the last daily pass. This is for the different,
more immediate question: **what's actually running on the cluster right
now, and how long has each case been going?** That requires a fresh
SSH/`ps` query at the moment you're asked, not anything cached or logged.

## Running it

```bash
python3 scripts/generate_grid_status.py
```

This does everything in one step:

1. SSHes into every known cluster machine once (`harvest_completed_cases.DEFAULT_MACHINES`,
   the same 14-machine list `harvest_completed_cases.py` uses) and runs a real
   `ps -eo pid,etimes,etime,args` query for live `proteus start` processes --
   genuine liveness and runtime, not a log-mtime guess.
2. Resolves each live case back to its position in the fO2 x H x C x N x S
   grid:
   - **Main-grid cases** (from a `batch_configs/batchNN.toml`): looked up
     against `harvest_completed_cases.discover_batches()` to get the batch
     label, then the case's own launch config
     (`<output>/cfgs/case_NNNNNN.toml`) is read **locally** (see "Why no SSH
     for config reads" below) and matched against
     `full_parameter_sweep.toml`'s axis lists via
     `harvest_completed_cases.match_index()`.
   - **Gapfill cases** (from `generate_gapfill_configs.py`, directory name
     `batch_gapfill_H<i>_C<i>_N<i>_S<i>_fO2<i>`): grid indices are read
     straight off the directory name, no config read needed.
3. Prints a plain-text summary to stdout (total running, per-machine and
   per-batch breakdown, which batches have zero running cases right now,
   longest-running case).
4. Writes a self-contained HTML report (`--output`, default
   `grid_status_report.html` in the project root) — a sortable/filterable
   table with per-row runtime bars and CPK-inspired colored chips for each
   grid axis (H/C/N/S/fO2). No server needed; opens directly in a browser.

Useful flags: `--summary-only` (skip writing the HTML), `--json PATH` (also
dump the raw rows array), `--machines cap001c,cap001d` (scope the SSH check
to specific machines, e.g. for a faster check when you already know which
machines matter).

## Publishing it as a shareable link

The script only writes a local file — it has no access to Claude Code's
`Artifact` tool, which is the only thing that can mint a
`claude.ai/code/artifact/...` URL. After running the script, publish its
output yourself:

```
Artifact(file_path=<the --output path>, title="K2-18 b Grid Sweep — Live Run Status",
         favicon="🌋", ...)
```

If you're updating a report you already published earlier in the same
conversation, republish the **same** `file_path` to redeploy to the same
URL rather than minting a new one; if it was published in an earlier
conversation, pass that artifact's `url` instead (see the `Artifact` tool's
own instructions for the full update flow).

## Why no SSH for config reads

`/data/rdc49-2/PROTEUS/output/` (where every batch's `cfgs/case_NNNNNN.toml`
launch configs and case directories live) is on a shared NFS mount readable
directly from whichever host runs this script — confirmed by reading one
such file locally with no SSH involved. SSH is only needed for the
liveness/runtime check itself, since the actual `proteus start` *processes*
only exist on the 14 cluster machines, not on this filesystem. Don't
"fix" the local `Path.read_text()`/`tomllib` calls in
`generate_grid_status.py` into SSH round-trips — that would just be slower
and was in fact how this was first done by hand before this fact was
noticed.

## Design notes if editing `scripts/grid_status_template.html`

- The template has three placeholders substituted by the script:
  `__ROWS_JSON__` (the rows array), `__ALL_BATCHES_JSON__` (every batch
  label from `discover_batches()`, used to compute "which batches have zero
  running cases right now" client-side so that note never goes stale), and
  `__AS_OF__` (the snapshot timestamp string).
- Row schema: `group` ("main" or "gapfill"), `batch`, `machine`, `case`,
  `etimes` (int seconds, used for sorting), `etime` (raw `ps` string, not
  directly displayed), `hi`/`ci`/`ni`/`si`/`foi` (0-3 grid indices), `H`/`C`/
  `N`/`S`/`fO2` (actual physical values).
- The active-machine chip strip and the "batches with zero running cases"
  footer note are both computed dynamically from the data at render time
  (not hardcoded) — the set of active machines and idle batches changes
  every time this is run, so don't reintroduce a fixed machine/batch list
  into the template.
- Design (dark "mission control" theme, CPK-inspired element colors for the
  H/C/N/S/fO2 chips, sortable columns, runtime bars) was reviewed and
  approved by the user via a published artifact — preserve it rather than
  redesigning from scratch on a future edit.
