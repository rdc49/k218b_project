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

- `simulation_data/` — raw output from PROTEUS grid-sweep runs (fO2 x H x C x
  N x S), one subdirectory per sweep (each containing one `case_NNNNNN/`
  subdirectory per grid point), following the PROTEUS `output/<run>/` layout
  described below. PROTEUS itself never writes here: grid sweeps run in a
  separate PROTEUS working directory, and completed sweep folders are only
  moved into `simulation_data/` by hand (via `scripts/move_sweep.py`) after
  being checked over. So absence of a run here doesn't mean it hasn't
  finished — it may just not have been reviewed/moved yet. Treat this as
  **read-only input** to plotting scripts — never hand-edit simulation
  output.
- `plotting_scripts/` — Python scripts that read from `simulation_data/` and
  produce figures. Currently empty. Each script's output figures should be
  written into a subdirectory of `paper/Figures/`.
- `scripts/` — project-management utility scripts (not paper-figure
  scripts; those go in `plotting_scripts/`). Currently contains
  `move_sweep.py`, which moves a completed sweep folder from the PROTEUS
  output directory into `simulation_data/` (see "Moving a completed sweep"
  below).
- `grid_sweep_configs/` — `proteus grid` config files (`.toml`) defining
  parameter sweeps, as distinct from `k218b_fiducial.toml` (the per-run base
  config each sweep point derives from). `full_parameter_sweep.toml` is the
  reference definition of the complete fO2 x H x C x N x S grid (1024
  points: 4 values per axis) — it is **not** meant to be submitted as a
  single `proteus grid` run. `batch_configs/batch01.toml`..`batch08.toml`
  split it into 8 batches of 128 points each (split by `fO2_shift_IW`, 4
  values, x `H_budget` low/high half, 2 groups; each batch keeps the full
  C/N/S axes).
  - **Batch size is cluster-aware, not arbitrary.** `proteus grid` clamps
    its concurrency to `min(max_jobs, batch_size, os.cpu_count())`
    (`PROTEUS/src/proteus/grid/manage.py:367-370`) — `max_jobs=500` in
    every batch file is already effectively unlimited, so batch *size*
    (grid-point count) is the only thing that determines whether a
    machine's cores sit idle. A batch smaller than the launching machine's
    core count leaves the difference permanently idle for the whole run;
    an oversized batch never wastes cores — it just runs more internal
    waves and takes proportionally longer on a smaller machine. 128 was
    chosen because it exceeds the core count of every machine on the IoA
    cluster (14 machines, 24-112 cores each; `cap005a` is the largest at
    112 — see `grid_sweep_cluster_howto.md`), so no batch ever idles cores
    no matter which of the 14 machines runs it. Don't shrink batch size
    below the largest machine's core count without re-checking this
    reasoning.
  - The batches exactly partition the full grid — every batch's `output`
    folder name is unique and the union of all 8 batches' grid points
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
  fO2 x H x C x N x S sweep.
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

### Moving a completed sweep into `simulation_data/`

The live PROTEUS install on this machine is `/data/rdc49-2/PROTEUS/`, and grid
sweeps run there land in `/data/rdc49-2/PROTEUS/output/<sweep_name>/`. A
sweep directory contains one subdirectory per grid point, `case_000000/`,
`case_000001/`, etc., each with:

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
prompt). It defaults to `/data/rdc49-2/PROTEUS/output/` as the source and
`simulation_data/` as the destination:

```bash
python3 scripts/move_sweep.py <sweep_name>
```

Equivalent manual steps, if not using the script: tally status labels
across a sweep with

```bash
for f in /data/rdc49-2/PROTEUS/output/<sweep_name>/*/status; do tail -n1 "$f"; echo; done | sort | uniq -c
```

then, once satisfied, move the whole sweep folder in one go — `PROTEUS/`
and `K218b_project/` are on the same filesystem (`/data/rdc49-2/`), so `mv`
is a cheap rename, not a copy:

```bash
mv /data/rdc49-2/PROTEUS/output/<sweep_name> /data/rdc49-2/K218b_project/simulation_data/
```

Either way, this preserves the full `case_NNNNNN/` structure, which is what
`plotting_scripts/` should expect to read. Because `simulation_data/*` is
gitignored (see `.gitignore`), moving a sweep in has no effect on git
history — it will not show up in `git status`.

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
