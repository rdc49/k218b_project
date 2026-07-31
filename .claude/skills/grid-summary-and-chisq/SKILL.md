---
name: grid-summary-and-chisq
description: Use when rebuilding simulation_data/grid_master.csv or adding/updating the chisq_* spectral-fit columns — scripts/build_grid_summary.py and scripts/compute_spectral_chisq.py. Covers the critical run-order gotcha (running build_grid_summary.py alone after compute_spectral_chisq.py silently wipes the chi-squared columns) and the pandas dtype gotcha on source_case_number.
---

# Building `simulation_data/grid_master.csv` and its chi-squared columns

`scripts/build_grid_summary.py` rebuilds `simulation_data/grid_master.csv`
from scratch every run: one row per point in the full 1024-point grid,
always, identified by `grid_index_<H,C,N,S,fO2>` + physical-value columns
computed directly from `full_parameter_sweep.toml` — populated for every
row regardless of whether that point has run yet. Only genuinely-successful
`1_success` cases populate the rest of a row, with that case's *last*
`runtime_helpfile.csv` row — crashed/killed cases are left blank, same as
never-attempted points, since their last row is just wherever the sim
died, not a finished state. Reuses
`harvest_completed_cases.load_full_grid_axes()` and
`generate_gapfill_configs`'s folder-name parsing/outcome classification
rather than reimplementing them (see the `harvest-batches` and
`recover-interrupted-grid-points` skills).

`scripts/compute_spectral_chisq.py` adds 8 `chisq_*` columns to the same
`grid_master.csv` — 4 raw + 4 paired `chisq_*_reduced` (one pair per
observed spectrum in `k218b_spectra/`), comparing each successful case's
synthetic `offchem` transit spectrum against it via `numpy.interp` + a
chi-squared sum. The reduced columns divide by dof = N (that source's own
observed-point count, k=0, since grid points are fixed forward-model
evaluations rather than fits) — see its own docstring for exactly which
observed files it uses, the overlap trims applied, and the dof rationale.

**Run order matters — this is the one gotcha to remember**: `build_grid_summary.py`
*also* fully rebuilds `grid_master.csv` from scratch, so running it alone
after `compute_spectral_chisq.py` would silently wipe the 8 chi-squared
columns back out. **Run `compute_spectral_chisq.py` instead of (or after)
`build_grid_summary.py` whenever the chi-squared columns are wanted** —
`compute_spectral_chisq.py` avoids the wipe-out problem itself by calling
`build_grid_summary.build_summary()` directly for a fresh base table
rather than reading the CSV off disk, so it alone always produces the
complete 481-column file (473 base + 4 raw chisq + 4 reduced chisq).

**Reading the output CSV back with pandas**: pass
`dtype={'source_case_number': str}` (or similar) — left as a bare
`pd.read_csv()`, pandas infers that column as float, silently dropping the
leading zeros from e.g. `case_000000`.
