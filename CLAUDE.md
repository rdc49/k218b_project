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
  N x S). Currently empty; will hold one subdirectory of PROTEUS output per
  grid point (or per sweep), following the PROTEUS `output/<run>/` layout
  described below. PROTEUS itself never writes here: grid sweeps run in a
  separate PROTEUS working directory, and completed sweep folders are only
  moved into `simulation_data/` by hand after being checked over. So absence
  of a run here doesn't mean it hasn't finished — it may just not have been
  reviewed/moved yet. Treat this as **read-only input** to plotting scripts —
  never hand-edit simulation output.
- `plotting_scripts/` — Python scripts that read from `simulation_data/` and
  produce figures. Currently empty. Each script's output figures should be
  written into a subdirectory of `paper/Figures/`.
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
  - `outgas.module = "atmodeller"` (not `CALLIOPE` — chosen to account for
    non-ideal fugacity effects). Note some exploratory sweep folders in
    PROTEUS `output/` use a `calliope`-suffixed variant instead; check which
    outgassing backend a given sweep used before comparing it against others.
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
  (Benneke 2019), `star.mass = 0.495 M_sun` (Cloutier 2019), `star.mors.Teff
  = 3645 K` / `rot_period = 39.3 d` / `age_now = 3.0 Gyr` (Sairam 2025),
  `orbit.semimajoraxis = 0.15910 au` (Benneke 2019). Host spectrum is
  MUSCLES GJ 849 used as an M-dwarf substitute for K2-18.
- **Grid axes live under these keys** — the fO2/H/C/N/S sweep should override:
  - `outgas.fO2_shift_IW` (fiducial: `-3`)
  - `planet.elements.H_budget` with `H_mode = "ppmw"` (fiducial: `10000`
    ppmw, ~1% EMF)
  - `planet.elements.C_budget` (fiducial: `0.32`, ~100x solar metallicity)
  - `planet.elements.N_budget` (fiducial: `0.09`, ~100x solar metallicity)
  - `planet.elements.S_budget` (fiducial: `0.04`, ~100x solar metallicity)

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

- `status` — one-line run state, e.g. `Running`, `Completed (solidified)`,
  `Completed (net flux is small)`, or `Error (...)`. **Check this in every
  case before moving the sweep** — a folder isn't ready to move while any
  case still says `Running` (still in progress) or has no `status` file at
  all (never started/crashed early). `Error (...)` cases are not failures of
  the move itself, but note them so the analysis in `plotting_scripts/`
  knows which grid points to exclude/flag.
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

To check status across a whole sweep before moving it:

```bash
for f in /data/rdc49-2/PROTEUS/output/<sweep_name>/*/status; do cat "$f"; echo; done | sort | uniq -c
```

Once every case reports a terminal state you're satisfied with (no
unexpected `Running` or missing-status cases), move the whole sweep folder
into `simulation_data/` in one go — `PROTEUS/` and `K218b_project/` are on
the same filesystem (`/data/rdc49-2/`), so `mv` is a cheap rename, not a
copy:

```bash
mv /data/rdc49-2/PROTEUS/output/<sweep_name> /data/rdc49-2/K218b_project/simulation_data/
```

This preserves the full `case_NNNNNN/` structure, which is what
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
