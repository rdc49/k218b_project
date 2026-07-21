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

PROTEUS is a coupled atmosphere-interior evolution framework: it couples an
interior thermal evolution model (`SPIDER` or `ARAGOG`) with a 1D
radiative-convective climate model (`AGNI`, backed by `SOCRATES`), with
volatile partitioning between magma and atmosphere handled by `CALLIOPE`
(equilibrium gas chemistry + solubility laws), and stellar evolution by
`MORS`. A run starts fully molten and integrates forward until either the
mantle solidifies or the planet reaches global energetic steady state
(instellation = internal heat + thermal emission) — the latter is the
"permanent magma ocean" outcome this project is testing for K2-18 b.

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
the physical output. When a checked sweep folder is moved into
`simulation_data/`, keep enough of that per-run structure intact (or flatten
it consistently via a documented convention) that `plotting_scripts/` can
find run metadata (grid parameter values), the physical output, and the
synthetic spectrum together for each point.

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
