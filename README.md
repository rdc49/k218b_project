# K2-18 b: Testing the Molten Gas Dwarf Hypothesis

Testing the molten gas dwarf hypothesis for K2-18 b with the `PROTEUS`
coupled interior-atmosphere evolution model. We sweep mantle oxygen fugacity
and the bulk volatile hydrogen, carbon, nitrogen and sulphur budgets,
generate synthetic transmission spectra for each grid point, and compare
these against observed K2-18 b transmission spectra to assess whether the
observations are consistent with K2-18 b being a "gas dwarf" (a
silicate/iron interior with an H$_2$-dominated envelope) with a permanent
magma ocean.

This project is the direct follow-up to a predecessor population-level study
(`reference_papers/calder_2026.pdf`), which established the "solidification
shoreline" concept and identified K2-18 b as a case warranting a dedicated,
self-consistent spectral comparison rather than mass/radius/instellation
arguments alone.

## Repository layout

- `k218b_fiducial.toml` — the fiducial PROTEUS configuration for the K2-18 b
  system. Every grid-sweep run is this file with the swept parameters
  (`outgas.fO2_shift_IW`, `planet.elements.{H,C,N,S}_budget`) overridden.
- `grid_sweep_configs/` — `proteus grid` config files defining the fO2 x H x
  C x N x S parameter sweep: `full_parameter_sweep.toml` is the reference
  1024-point grid definition (not launched directly); `batch_configs/` splits
  it into 16 cluster-sized batches of 64 points each.
- `raw_grid_output/` — staging area sweep output is intended to land in
  directly on this project's own disk (currently unused pending an upstream
  PROTEUS symlink bug fix — see `CLAUDE.md`).
- `simulation_data/` — raw output from completed PROTEUS grid-sweep runs,
  moved/harvested in after being checked over. Contents are gitignored
  (large binary simulation output) except `GRID_INDEX_LEGEND.txt`, an
  auto-generated file mapping each harvested case's grid-position indices
  (e.g. `H0`, `C1`) to their real physical parameter values.
- `scripts/` — project-management utilities: `move_sweep.py` (move one
  finished sweep into `simulation_data/`), `harvest_completed_cases.py`
  (monitor in-progress batches and incrementally pull out finished cases,
  scheduled daily via cron), `generate_gapfill_configs.py` (regenerate
  single-point configs for grid points interrupted by the cluster's weekly
  reset), and `analyze_grid_sweep.py` (per-sweep analysis tool). See
  `CLAUDE.md` for full usage of each.
- `plotting_scripts/` — Python scripts that read `simulation_data/` and
  produce the figures used in the paper.
- `paper/` — MNRAS-format LaTeX source for the resulting paper (`main.tex`,
  `references.bib`, `Figures/`).
- `reference_papers/` — supporting PDFs, including the predecessor
  population study.
- `grid_sweep_cluster_howto.md` — how to launch and monitor sweeps on the
  IoA cluster (machine specs, load-checking, `nice` usage).
- `CLAUDE.md` — detailed technical notes on the PROTEUS workflow used by
  this project (module selection, grid-sweep-to-`simulation_data/` workflow,
  cluster harvesting/recovery, paper compilation) for anyone (human or
  AI-assisted) picking up the project.

## Model

Simulations are run with [PROTEUS](https://github.com/FormingWorlds/PROTEUS),
an open-source coupled interior-atmosphere evolution framework. For this
project, PROTEUS is configured with the `zalmoxis` interior structure module,
a parametrised `boundary`-layer interior energetics module, the `atmodeller`
outgassing module (accounting for non-ideal fugacity effects), the `agni`
radiative-convective climate module (backed by `SOCRATES`), the `zephyrus`
escape module, `mors` stellar evolution, and `petitRADTRANS` for synthetic
transmission spectra, with `vulcan` atmospheric chemistry run offline. See
`k218b_fiducial.toml` and `CLAUDE.md` for details.

## Compiling the paper

```bash
cd paper
module load texlive/2017   # or point directly at /opt/ioa/texlive/2017/bin/x86_64-linux/
pdflatex -interaction=nonstopmode main.tex
bibtex main
pdflatex -interaction=nonstopmode main.tex
pdflatex -interaction=nonstopmode main.tex
```

See `LATEX_COMPILE_GUIDE.md` for machine-specific details and troubleshooting.

## Status

Work in progress: the fO2 x H x C x N x S parameter sweep is being run
across the IoA cluster in 16 batches, monitored and harvested into
`simulation_data/` incrementally as cases finish. `paper/main.tex` currently
contains a placeholder structure pending completed grid-sweep results.
