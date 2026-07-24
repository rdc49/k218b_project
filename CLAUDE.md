# K2-18 b Molten Gas Dwarf Project

## Goal

Test the **molten gas dwarf hypothesis** for the sub-Neptune K2-18 b using the
`PROTEUS` coupled interior-atmosphere evolution model. The plan:

1. Run PROTEUS with fiducial parameters appropriate for the K2-18 b system
   (planet mass/radius, instellation, host star spectrum, etc.), sweeping a grid
   over:
   - mantle oxygen fugacity (fO2)
   - bulk volatile **hydrogen** budget
   - bulk volatile **carbon** budget
   - bulk volatile **nitrogen** budget
   - bulk volatile **sulphur** budget
2. Atmospheric chemistry and the synthetic transmission spectrum for each
   grid point are generated automatically as part of the PROTEUS pipeline
   itself (not a separate post-processing step) and land in that run's own
   output subdirectory.
3. Compare synthetic spectra against one or more observed K2-18 b transmission
   spectra.
4. Assess whether the observations are consistent with K2-18 b being a molten
   gas dwarf (permanent magma ocean + H2-dominated envelope), or whether they
   rule it out.

This project is the natural follow-up to the predecessor population-level
study in `reference_papers/calder_2026.pdf` (source in `paper/`), which
established the "solidification shoreline" concept and explicitly flagged that
an evolutionary, self-consistent comparison of K2-18 b against its observed
spectrum (rather than mass/radius/instellation alone) was the needed next
step — that is this project.

## Directory layout

- `raw_grid_output/` — staging area that batch grid configs' `symlink`
  field points PROTEUS at directly, so sweep output is written here (on
  this project's own disk) rather than under PROTEUS's own `output/`
  directory. Not yet reviewed/checked — see "Where sweep output actually
  lands" below before treating anything here as trustworthy.
- `simulation_data/` — raw output from PROTEUS grid-sweep runs (fO2 x H x C x
  N x S), one subdirectory per sweep (each containing one `case_NNNNNN/`
  subdirectory per grid point), following the PROTEUS `output/<run>/` layout
  described below. PROTEUS itself never writes here directly: completed
  sweep folders are only moved here by hand (via `scripts/move_sweep.py`,
  from `raw_grid_output/`) after being checked over. So absence of a run
  here doesn't mean it hasn't finished — it may just not have been
  reviewed/moved yet. Treat this as **read-only input** to plotting
  scripts — never hand-edit simulation output.
- `plotting_scripts/` — Python scripts that read from `simulation_data/` and
  produce figures. Currently empty. Each script's output figures should be
  written into a subdirectory of `paper/Figures/`.
- `scripts/` — project-management utility scripts (not paper-figure
  scripts; those go in `plotting_scripts/`). Contains `move_sweep.py`
  (moves one whole, fully-finished sweep folder into `simulation_data/`;
  see "Moving a completed sweep" below), `harvest_completed_cases.py`
  (prints a cross-batch status summary and incrementally pulls individual
  finished cases out of still-running batches, renamed by grid position;
  cross-checks each case's log rather than trusting its on-disk `status`
  alone — see "Monitoring and harvesting while batches are still running"
  below), and `analyze_grid_sweep.py` (a separate, pre-existing, more
  thorough per-sweep analysis tool, not written by this assistant, whose
  log-cross-checking approach `harvest_completed_cases.py` reuses/adapts —
  see its own docstring for the single-sweep-focused analysis and
  plot-collection features it has that `harvest_completed_cases.py`
  doesn't).
- `grid_sweep_configs/` — `proteus grid` config files (`.toml`) defining
  parameter sweeps, as distinct from `k218b_fiducial.toml` (the per-run base
  config each sweep point derives from). `full_parameter_sweep.toml` is the
  reference definition of the complete fO2 x H x C x N x S grid (1024
  points: 4 values per axis) — it is **not** meant to be submitted as a
  single `proteus grid` run. `batch_configs/batch01.toml`..`batch16.toml`
  split it into 16 batches of 64 points each (split by `fO2_shift_IW`, 4
  values, x `H_budget` low/high half, 2 groups, x `C_budget` low/high half,
  2 groups; each batch keeps the full N/S axes). Each batch also sets
  `symlink` to redirect its output into `raw_grid_output/` — see "Where
  sweep output actually lands" below.
  - **Batch size trades off two competing cluster-aware goals, not an
    arbitrary number.** `proteus grid` clamps its concurrency to
    `min(max_jobs, batch_size, os.cpu_count())`
    (`PROTEUS/src/proteus/grid/manage.py:367-370`) — `max_jobs=500` in
    every batch file is already effectively unlimited, so batch *size*
    (grid-point count) is what determines whether a machine's cores sit
    idle. A batch smaller than the launching machine's core count leaves
    the difference permanently idle for the whole run; an oversized batch
    never wastes cores — it just runs more internal waves and takes
    proportionally longer on a smaller machine. Given that, there are two
    things to optimise for on the IoA cluster (14 machines, 24-112 cores
    each; `cap005a` is the outlier at 112, the other 13 are 24 or
    48 — see `grid_sweep_cluster_howto.md`): (1) no single batch should
    idle cores on whichever machine runs it, and (2) there should be
    enough independent batches to occupy most/all 14 machines at once.
    These pull in opposite directions given the fixed 1024-point grid: an
    earlier version of this file sized every batch at 128 (above
    `cap005a`'s 112 cores), satisfying (1) perfectly but only yielding 8
    batches total — capping simultaneous cluster usage at 8 of 14 machines
    no matter how many were actually free. The current 64-point size is
    instead sized above the *typical* machine (48 cores, covering 13 of
    the 14 machines), giving 16 batches — enough for all 14 machines with
    2 spare. The accepted trade-off: a single 64-point batch landing on
    `cap005a` only fills 64 of its 112 cores, leaving 48 idle for that
    batch's duration — a much smaller loss than leaving whole machines
    idle, so the preferred one here. If `cap005a`'s spare capacity matters
    for a given push, launch a second batch there concurrently in a
    separate tmux session rather than leaving it under-filled. Don't
    shrink batch size below 48 (the typical machine's core count) without
    re-checking this reasoning, and don't grow it back toward 112+ without
    remembering that re-caps simultaneous machine usage.
  - The batches exactly partition the full grid — every batch's `output`
    folder name is unique and the union of all 16 batches' grid points
    equals the full grid with zero overlap (verified programmatically when
    the batches were created/re-split). If the full sweep definition, the
    batch size, or the cluster's machine specs ever change, regenerate the
    batch files from scratch rather than hand-editing them independently,
    to keep the partition exact and the sizing reasoning valid.
- `paper/` — MNRAS-format LaTeX source for the resulting paper (`main.tex`,
  `references.bib`, `mnras.cls`/`mnras.bst`, `Figures/`). It currently
  contains the predecessor paper (`calder_2026`) as a starting template/style
  reference — expect to substantially rewrite `main.tex`/`references.bib` for
  the K2-18 b-specific results rather than treat the current content as final.
- `reference_papers/` — supporting PDFs, e.g. `calder_2026.pdf` (the
  predecessor paper above).
- `k218b_fiducial.toml` — the fiducial PROTEUS configuration for this
  project: every non-default parameter is set explicitly with an inline
  comment justifying the choice. This is the base config that the grid sweep
  is built from — each grid point's config should equal this file with only
  the swept fO2/H/C/N/S keys overridden. See "Fiducial configuration" below
  for the specifics it pins down.
- `proteus_CLAUDE.md` — full agent-guidelines file for *developing* the
  PROTEUS codebase itself (installation, testing rigor, coverage gates, PR
  process, etc.). It is not this project's own instructions; it's carried
  here for reference on how PROTEUS works and how to run it. Consult it when
  configuring runs, interpreting output, or if something in a PROTEUS
  submodule needs debugging — but its testing/coverage/PR rules apply to
  PROTEUS-the-codebase, not to this analysis project.
- `LATEX_COMPILE_GUIDE.md` — machine-specific instructions for compiling
  `paper/main.tex` on this machine (see below).

## PROTEUS essentials for this project

PROTEUS is a coupled atmosphere-interior evolution framework built from
swappable modules per physics domain (interior structure, interior
energetics, outgassing, climate, escape, observation, atmospheric
chemistry). `SPIDER`/`ARAGOG` + `CALLIOPE` is the generic/default module
combination described in `proteus_CLAUDE.md`, but **this project uses a
different combination** — see "Fiducial configuration" below for the exact
modules `k218b_fiducial.toml` selects. A run starts fully molten and
integrates forward until either the mantle solidifies or the planet reaches
global energetic steady state (instellation = internal heat + thermal
emission) — the latter is the "permanent magma ocean" outcome this project
is testing for K2-18 b.

Relevant CLI entry points (see `proteus_CLAUDE.md` for full detail):

- `proteus start -c <config.toml>` — run a single simulation.
- `proteus grid` — run a parameter grid/sweep. This is what generates the
  sweep folders that get moved into `simulation_data/` (see above) for the
  fO2 x H x C x N x S sweep. **Always launch it as `nice -n 19 proteus
  grid -c <config>`, never bare `proteus grid`.** The IoA cluster machines
  this project's batches run on (see `grid_sweep_cluster_howto.md`) are
  shared with other people, not dedicated to this project; `nice -n 19`
  (lowest scheduling priority) makes a sweep's CPU-bound worker
  subprocesses yield to anyone else's work on the same node rather than
  compete with it on equal footing. The workers are forked from the niced
  parent process and inherit its priority automatically, so niceing just
  the `proteus grid` invocation itself is enough — no need to nice each
  case individually. This is a courtesy floor, not a substitute for
  checking a machine's load before picking it (see the howto's "Choosing a
  machine" section).
- `proteus plot -c <config.toml> all` — PROTEUS's own built-in diagnostic
  plots (distinct from the custom `plotting_scripts/` figures for the paper).
- `proteus doctor` — environment diagnostics if a run environment looks broken.

Configuration is via TOML. Every config must set an explicit
`planet.elements.O_mode` (`"ic_chemistry"`, `"ppmw"`, `"kg"`, or
`"FeO_mantle_wt_pct"`) governing how oxygen is accounted for relative to the
mantle fO2 buffer — this directly interacts with the fO2 sweep axis of this
project, so pick the mode deliberately and keep it consistent across the grid
so that fO2 comparisons are apples-to-apples. The H/C/N/S volatile budgets for
the sweep are set the same way (`ppmw`/`kg` per element).

### Fiducial configuration: `k218b_fiducial.toml`

This is the authoritative base config for the project (project root). Every
grid-sweep config should derive from it, overriding only the swept
parameters. Key points:

- **Module selection** (differs from the generic PROTEUS description above —
  treat this file, not `proteus_CLAUDE.md`, as the source of truth for which
  modules this project actually uses):
  - `interior_struct.module = "zalmoxis"`
  - `interior_energetics.module = "boundary"` (a parametrised boundary-layer
    model, not `SPIDER`/`ARAGOG`)
  - `outgas.module = "atmodeller"` (not `CALLIOPE`). Note the fiducial config
    does not set any `outgas.atmodeller.eos_*` fields, so they sit at their
    schema default (`None` = ideal gas) — real-gas non-ideality in the
    outgassing/solubility equilibrium is *not* currently enabled, only in
    AGNI's atmosphere structure (`atmos_clim.agni.real_gas = true`, a
    separate, unrelated switch — see "PROTEUS gotchas" below). Also note some
    exploratory sweep folders in PROTEUS `output/` use a `calliope`-suffixed
    variant instead; check which outgassing backend a given sweep used before
    comparing it against others.
  - `atmos_clim.module = "agni"` (backed by `SOCRATES`, `spectral_group =
    "Dayspring"`, 48 bands)
  - `escape.module = "zephyrus"`
  - `observe.module = "petitRADTRANS"` — this is what generates each run's
    synthetic transmission spectrum.
  - `atmos_chem.module = "vulcan"`, run `when = "offline"` — still part of
    the automated PROTEUS pipeline (see above), just executed as an offline
    post-step rather than inline each timestep.
  - `star.module = "mors"` (stellar evolution included, not blackbody).
- **Fixed K2-18 b system parameters**: `planet.mass_tot = 8.63 M_Earth`
  (Cloutier 2019), `star.mass = 0.495 M_sun` (Cloutier 2019),
  `star.mors.rot_period = 39.3 d` / `age_now = 3.0 Gyr` (Sairam 2025),
  `orbit.semimajoraxis = 0.15910 au` (comment cites Benneke 2019; not fully
  confirmed against the primary source, lower-confidence than the other
  citations here). Host spectrum is
  MUSCLES GJ 849 used as an M-dwarf substitute for K2-18. Note there is no
  explicit Teff input: with `star.module = "mors"`, Teff is *derived* each
  step from the Spada evolutionary track as a function of stellar mass and
  the simulation's elapsed stellar age, not read from a config field — see
  "PROTEUS gotchas" below.
- **Grid axes live under these keys** — the fO2/H/C/N/S sweep should override:
  - `outgas.fO2_shift_IW` (fiducial: `-3`)
  - `planet.elements.H_budget` with `H_mode = "ppmw"` (fiducial: `10000`
    ppmw, ~1% EMF)
  - `planet.elements.C_budget` (fiducial: `0.32`, ~100x solar metallicity)
  - `planet.elements.N_budget` (fiducial: `0.09`, ~100x solar metallicity)
  - `planet.elements.S_budget` (fiducial: `0.04`, ~100x solar metallicity)

**IMPORTANT — this file is not what `proteus grid` actually reads.**
`grid_sweep_configs/*.toml` files set `ref_config =
"input/nogit_k218b_project/k218b_fiducial.toml"`, a path resolved relative
to the PROTEUS install root (`/data/rdc49-2/PROTEUS/`), i.e. a *separate
copy* living inside the live PROTEUS working directory, not this file.
These two copies can and do drift: as of 2026-07-22 the PROTEUS-side copy
still has the pre-review bugs (stale `mass_tot` citation, the dead
`[star.dummy]` Teff block, `solubility_H2O = "H2O_basalt_dixon95"`, the
`outgas.atmodeller.eos_* = "none"` overrides) that were already fixed in
this file. **Before launching any grid/batch run, diff this file against
`/data/rdc49-2/PROTEUS/input/nogit_k218b_project/k218b_fiducial.toml` and
copy this one over if they differ** — otherwise fixes made here silently
never reach the actual simulations. Same applies to any other fiducial
variant referenced by a grid config's `ref_config` (e.g. a
`calliope`-backend equivalent).

### PROTEUS gotchas (learned from reviewing the fiducial config)

These generalise beyond this one config file — useful whenever writing or
reviewing any PROTEUS TOML for this project:

- **Module-conditional subtables are silently inert, not errors.** Every
  physics domain has a `<domain>.module` selector plus one subtable per
  possible module (e.g. `star.mors` / `star.dummy`,
  `interior_energetics.boundary` / `.spider` / `.aragog`, `atmos_clim.agni` /
  `.janus` / `.dummy`). Only the subtable matching the selected module is
  ever read; the others load and validate fine but have **zero effect** on
  the run. This bit us once already: `star.dummy.Teff` was set (correctly,
  citing Sairam 2025) while `star.module = "mors"`, so it was never read —
  Teff was actually being derived from the `mors` Spada evolutionary track
  instead. Before trusting a value in the TOML, check it lives under the
  subtable matching the active `module`.
- **Instellation flux is scaled by `orbit.s0_factor * cos(orbit.zenith_angle)`**
  (same formula in `atmos_clim/{agni,janus,dummy}.py`, `star/wrapper.py`, and
  the `plot/cpl_*.py` scripts). The PROTEUS schema default
  (`s0_factor=0.375`, `zenith_angle=48.19°`) gives ≈0.25 — the standard
  whole-planet, time-and-latitude-averaged S/4 factor, and is what the
  predecessor Calder 2026 population study implicitly used. This project's
  fiducial config instead uses `s0_factor=1.0`, `zenith_angle=45°` (≈0.71,
  a "dayside" assumption) — about 2.8x more effective irradiation for the
  same nominal instellation flux. Any comparison of `F_ins` values against
  the predecessor paper or against literature that assumes global averaging
  needs to account for this factor explicitly.
- **`star.mors.age_now` is not the simulation's starting stellar age.** It
  only anchors/calibrates the rotation-activity evolutionary track against
  the real, observed (age, rotation period) pair (`star/wrapper.py`,
  `mors.Star(Age=age_now_Myr, Prot=rot_period)`). The age actually used to
  evaluate Teff/luminosity/XUV flux at each coupling step
  (`hf_row['age_star']`) starts at `star.age_ini` (schema default 0.1 Gyr)
  and increments by the elapsed simulation time — it is *not* anchored to
  reach `age_now`. If a run's total elapsed time stays well under `age_now`,
  the star's properties used for most of the run reflect an early point on
  its evolutionary track, not the present-day system. Set `star.age_ini`
  deliberately if the run needs to represent the star at its current age.
- **`outgas.atmodeller.solubility_*` fields are free strings, not a
  validated enum** — a typo won't be caught at config-load, only at the
  first outgas call. The registry of valid names lives in the installed
  package at `atmodeller/solubility/library.py` (conda env:
  `.../envs/proteus/lib/python3.12/site-packages/atmodeller/solubility/`).
  Grep that file to confirm a solubility-law name before using it in a
  config.
- **Real-gas EOS is two independent switches.** `atmos_clim.agni.real_gas`
  controls the atmosphere height-structure EOS; `outgas.atmodeller.eos_*`
  (per-species) controls the EOS used in the outgassing/melt-atmosphere
  equilibrium solve. Enabling one does not enable the other — check both if
  non-ideal-gas behaviour matters for a given run.
- **`interior_struct.core_frac` means different things depending on
  `core_frac_mode`.** Defaults to `'mass'` for the `zalmoxis` structure
  module (interprets `core_frac` as a core *mass* fraction — Earth's PREM
  value is ≈0.325) vs `'radius'` (required for the `spider` module — Earth's
  core *radius* fraction is ≈0.55). Both are legitimate "Earth reference
  values" for different quantities; don't compare `core_frac` numbers across
  configs without checking which mode each one uses.
- **`params.stop.solid.phi_crit` and `interior_energetics.rfront_loc` can be
  deliberately linked.** The fiducial config sets both to `0.4`: for the
  `boundary` energetics module, this defines "solidified" as "melt fraction
  has dropped below the rheological lock-up point" rather than "≈0% melt"
  (schema default `phi_crit=0.01`). This is a materially different
  solidification threshold than the predecessor Calder 2026 study used
  (~1% melt) — keep this in mind when comparing solidification outcomes or
  timescales between the two papers.
- **`interior_struct.zalmoxis.update_interval = 0` disables dynamic
  structure re-solving** during a coupled run (structure is solved once at
  initialisation only); this is the documented way to avoid the Zalmoxis
  structure solver conflicting with the `boundary` energetics module.
- **To check whether a config value is valid or meaningful, read the schema
  directly** rather than guessing: each top-level TOML section has a
  matching attrs class in `PROTEUS/src/proteus/config/_<section>.py` (e.g.
  `_planet.py`, `_star.py`, `_orbit.py`, `_struct.py`, `_interior.py`,
  `_outgas.py`, `_atmos_clim.py`). Cross-field validation (e.g. "boundary
  liquidus must exceed solidus", "mors needs exactly one of rot_pcntle /
  rot_period") is almost always gated behind `if instance.module != 'X':
  return`, so it only fires for the module actually selected — grep the
  field name in the relevant file to see its default, validator, and any
  module-specific constraints.

Long grid runs should be launched detached, e.g.:

```bash
nohup proteus start -c <cfg.toml> --offline > output/<run>/launch.log 2>&1 & disown
```

Never foreground a multi-hour run. Use `-r`/`--resume` to continue an
interrupted run, and `--deterministic` if numerically fragile runs need
bit-reproducibility.

Simulation output for a run lives under `output/<run>/` in a PROTEUS working
directory (gitignored there); the per-timestep history is in `hf_all`/`hf_row`
type records with periodic interior snapshots (`<iter>_int.nc`) under `data/`.
Atmospheric chemistry and the synthetic spectrum for each run are produced
automatically by the PROTEUS pipeline (no separate `observe`/`offchem` call
needed) and are written into that same per-run output subdirectory alongside
the physical output.

### Where sweep output actually lands: `raw_grid_output/` (currently disabled — see bug below)

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

### Moving a completed sweep into `simulation_data/`

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
  output generated automatically by the pipeline (see above).
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
case). Either way it moves the *real* directory — never the symlink itself
— into `simulation_data/`, and if a matching symlink was left behind under
`PROTEUS/output/<sweep_name>` pointing at the directory just moved, it
deletes that now-dangling symlink too.

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

### Monitoring and harvesting while batches are still running: `scripts/harvest_completed_cases.py`

`move_sweep.py` above moves one *entire* sweep folder at once, once every
case in it is done — the right tool once a batch has fully finished.
`scripts/harvest_completed_cases.py` is for the more common situation of
several batches running concurrently on different cluster machines, each
partway through its 128 cases: it (1) prints a per-batch status summary,
discovered dynamically from `grid_sweep_configs/batch_configs/batch*.toml`
(no hardcoded batch list to keep in sync), then (2) moves every individual
case with a genuinely terminal outcome straight into `simulation_data/`,
leaving still-running cases in their batch untouched. Safe to re-run
repeatedly (by hand, cron, or its own `--watch SECONDS` loop) —
already-harvested cases are simply gone from the source next time, and
moved-to destinations are never overwritten.

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

#### Aliveness-check bug found during the first real test (2026-07-22 to 2026-07-24)

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

**Renaming**: a harvested case is renamed from its batch-local
`case_NNNNNN` to
`grid_H<i>_C<i>_N<i>_S<i>_fO2<i>__from_<batch>_<case>__<outcome>`, where
each index (0-3) is that axis's position in `full_parameter_sweep.toml`'s
value list (read from the case's own `init_coupler.toml`, not recomputed
from batch/case numbering, so it's correct even if a batch's internal
split logic ever changes again), and `<outcome>` is the log-derived
category (`1_success`, `2_fatal_crash_main_loop`, `3_crash_observe_step`,
or `6_unclassified` for killed-externally), plus the matched failure
signature name when there is one, e.g.
`grid_H0_C2_N1_S3_fO20__from_batch01_case000042__2_fatal_crash_main_loop_atmodeller_overflow`.
The `grid_H.._C.._N.._S.._fO2..` prefix is what identifies the case's
position in the full 1024-point sweep; the rest is outcome/provenance,
kept for traceability and so failed vs. successful cases can be told apart
(e.g. via `ls simulation_data | grep 1_success`) without reopening each
case's log.

**Categories 4 and 5 (genuinely still running, whether or not `status`
agrees) are never harvested, and neither is category 6 with no
`proteus_00.log` at all** (ambiguous between never-started and
crashed-before-logging — left alone rather than guessed at).

**Known limitation, accepted rather than engineered around**: `proteus
grid`'s own manager does one final pass over every case's status file
right when a whole batch's last case finishes (end of `Grid.run()` in
`PROTEUS/src/proteus/grid/manage.py`), and raises if a status file is
missing. If this script harvests a case out of a batch in the same instant
that batch's very last case completes, that final pass could crash with a
traceback — but only after all of that batch's actual simulation work is
already done, so nothing is lost; the manager process's log just ends in
an exception instead of a clean finish. Narrow window, cosmetic impact,
not otherwise guarded against.

**A batch's own container directory isn't cleaned up.** Once every case in
a batch has been harvested, `raw_grid_output/<batch_output_name>/` still
exists (now containing only `cfgs/`, `logs/`, `manager.log`, etc., no more
`case_*` dirs) — harmless, but a manual `rm -r` once you're sure a batch is
fully drained is fine if you want to tidy it up.

### Scheduled harvesting via cron

`harvest_completed_cases.py` runs automatically every day at 07:00 (all
7 days), via this machine's user crontab (`crontab -l` to view,
`crontab -e` to edit). **It was paused (line commented out, not deleted)
from 2026-07-24 while the aliveness-check bug above was found and fixed,
then re-enabled the same day once the fix was verified against the live
batch01/batch02 processes.** If you ever need to pause it again (e.g.
while changing this script), comment the line out with a `#` prefix plus a
dated reason rather than deleting it, so it's easy to tell it was
deliberate and easy to restore — and always re-check the first few lines
of `crontab -l` against what they were before, to make sure the edit
didn't touch the two unrelated pre-existing jobs (see below).

```cron
0 7 * * * flock -n /tmp/k218b_harvest_cron.lock /data/rdc49-2/anaconda3/envs/proteus/bin/python3 /data/rdc49-2/K218b_project/scripts/harvest_completed_cases.py >> /data/rdc49-2/K218b_project/logs/harvest_cron.log 2>&1
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
- Output is appended to `logs/harvest_cron.log` (gitignored, like
  `simulation_data/` and `raw_grid_output/`) rather than left for cron's
  own mail-on-output behaviour, which may not even be configured on this
  machine. Check that file for history/errors, not system mail. The
  script itself prints timestamped start/finish/failure banners into that
  log (see its docstring) precisely so a day's entry is easy to find and
  an error is easy to grep for in a file that just keeps growing.
- The existing crontab already had two unrelated jobs (`daily_github_backup.py`,
  `backup_to_hardrive.py`) — this entry was appended, not replacing
  anything. If you ever need to edit the crontab, use `crontab -l` /
  `crontab -e` in place rather than regenerating it from scratch, to avoid
  disturbing those.

**Unrelated finding worth flagging** (same issue already noted for
`~/.bashrc` in `grid_sweep_cluster_howto.md`): the crontab has a
`GITHUB_TOKEN=...` line with a real personal access token in plaintext.
Don't propagate it anywhere; flag it to the user for rotation if noticed
again.

## Compiling the paper

Full details in `LATEX_COMPILE_GUIDE.md`; summary:

- No `pdflatex` on default `PATH` — load it with `module load texlive/2017`
  (newest available on this machine; a modern engine is not readily usable
  here — see the guide if that ever needs revisiting).
- Standard build sequence (always do all passes, a single `pdflatex` run is
  never enough with citations/cross-refs):
  ```bash
  cd paper
  module load texlive/2017
  pdflatex -interaction=nonstopmode main.tex
  bibtex main
  pdflatex -interaction=nonstopmode main.tex
  pdflatex -interaction=nonstopmode main.tex
  ```
- `\bibliography{references}` — **no** `.bib` extension (BibTeX appends it
  automatically; the wrong form fails silently with undefined citations).
- Verify success beyond exit code 0:
  ```bash
  grep -a -c "Citation .* undefined" main.log   # want 0
  grep -a -c "Rerun to get" main.log            # want 0
  ls -la main.pdf                               # fresh timestamp
  ```
  Always pass `-a` to `grep` on `.log` files — without it, matches can be
  silently missed.
- If a run crashes partway, `main.aux` can be left corrupted; don't trust the
  next run without a full clean rebuild (`rm -f main.aux main.bbl main.blg
  main.out main.pdf main.log` and rebuild from scratch).
- Known engine bug: a `\pdfendlink` fatal error can occur from natbib citation
  links landing on a page break. Workaround (only if hit) is in the guide —
  disables citation clickability, not the citation text/rendering.

## Working conventions

- Keep `simulation_data/` as an input-only store for raw PROTEUS grid output;
  do all analysis/transformation in `plotting_scripts/`.
- `plotting_scripts/` scripts should be self-contained and re-runnable against
  `simulation_data/` to regenerate any figure in `paper/Figures/` without
  needing to rerun PROTEUS.
- Save each script's output figures into `paper/Figures/` (a subdirectory per
  script/topic is fine) so `main.tex` can `\includegraphics` them directly.
- Since this is destined for publication, favour clear axis labels with
  units, a colourblind-safe palette, legends, and vector formats (PDF) for
  figures, consistent with MNRAS figure conventions.
