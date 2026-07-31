#!/usr/bin/env python3
"""Add spectral chi-squared columns to simulation_data/grid_master.csv --
one column per real observed K2-18 b spectrum, comparing each successful
grid point's synthetic transmission spectrum (offchem source, includes
VULCAN photochemistry) against it.

Why this exists
-----------------------------------------------------------------------
scripts/build_grid_summary.py already summarizes every successful grid
point's final physical state. This adds the actual science payoff: for
each grid point, how well does its synthetic spectrum match the real
planet, expressed as a chi-squared goodness-of-fit against three real
observed K2-18 b spectra (see k218b_spectra/, documented in CLAUDE.md).

Adds 8 columns (4 raw chisq_* + 4 paired chisq_*_reduced -- see below):
  chisq_madhu_2023           Madhusudhan et al. 2023: native NIRISS SOSS
                             + native NIRSpec G395H (trimmed at their
                             ~0.084um overlap), combined into one fit.
  chisq_madhu_2025_jexores   Madhusudhan et al. 2025 MIRI LRS, JExoRES
                             pipeline reduction.
  chisq_madhu_2025_jexopipe  Same underlying MIRI LRS spectrum, JexoPipe
                             pipeline reduction -- kept as a SEPARATE
                             column from jexores rather than combined,
                             since these are two alternative reductions
                             of the identical wavelength range (combining
                             them would double-count, not add coverage).
  chisq_hu_2026              Hu et al. 2026: NIRISS SOSS (order1 + order2,
                             order2 trimmed below order1's minimum since
                             they're two diffraction orders of the SAME
                             exposure) + NIRSpec G235H/G395H "direct"
                             combined-detector reductions (trimmed at
                             their ~0.2um mutual overlap) -- NOT the 4
                             "shifted" per-detector files in the same
                             directory, which are alternate reductions of
                             the identical data, not extra coverage.
                             NIRISS and NIRSpec G235H's wavelength ranges
                             do genuinely overlap (~1.7um) and are NOT
                             trimmed against each other, since they're
                             different instruments/gratings -- independent
                             measurements, normal to include jointly in a
                             multi-instrument chi-squared.

Chi-squared = sum( (observed_ppm - model_ppm)^2 / uncertainty_ppm^2 ),
raw, over every observed data point in that source after the trims
above. The model spectrum (native petitRADTRANS grid, ~0.05-300um) is
linearly interpolated (numpy.interp) onto each observed data point's
exact wavelength -- not bin-averaged, since the native grid's point
spacing is comparable to or coarser than some observed bins' widths,
making bin-averaging noisy/undefined in places; point-interpolation is
robust regardless of relative resolution and standard practice here.
All observed wavelengths fall well inside the synthetic grid's range,
so this never extrapolates.

Each raw chisq_* column is paired with a chisq_*_reduced column, =
chisq / N, where N is that source's own number of observed data points
after trims. dof = N (k=0): each grid point is a fixed forward-model
evaluation (fO2/H/C/N/S come from the grid, nothing is fitted to the
observed spectrum), so no parameters are subtracted off. This is what
makes the reduced values comparable across the four sources despite
their different N, and gives an absolute goodness-of-fit scale
(reduced chisq ~1 is a good fit) that the raw column doesn't.

Units: the observed files are a mix of fractional (Rp/Rs)^2 (Madhusudhan
data, hu_2026 nirspec_combined) and already-ppm (hu_2026 NIRISS ECSV
files) -- every source is converted to ppm before comparison, matching
the synthetic spectrum's own native ppm units.

Why this doesn't just add columns to the existing grid_master.csv
-----------------------------------------------------------------------
scripts/build_grid_summary.py fully rebuilds grid_master.csv from scratch
on every run (no read-modify-write) -- so a script that only appended
columns to the existing file would have its work silently wiped out the
next time someone re-runs build_grid_summary.py alone. Instead, this
script imports build_grid_summary.build_summary() directly for a fresh,
complete base DataFrame, adds the 8 chi-squared columns, and writes the
complete file itself. Run THIS script (not build_grid_summary.py alone)
whenever the chi-squared columns are wanted -- it produces the full
481-column file (473 base + 4 raw chisq + 4 reduced chisq);
build_grid_summary.py alone only produces the 473 base columns.

Usage:
    python3 scripts/compute_spectral_chisq.py [--dry-run] [--output PATH]

Example:
    python3 scripts/compute_spectral_chisq.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import harvest_completed_cases as hc  # noqa: E402  (needs sys.path set up first)
import build_grid_summary as bgs  # noqa: E402

PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_SPECTRA_DIR = PROJECT_ROOT / "k218b_spectra"
DEFAULT_OUTPUT = bgs.DEFAULT_OUTPUT

PPM_PER_FRACTION = 1e6

CHISQ_COLUMNS = [
    "chisq_madhu_2023",
    "chisq_madhu_2025_jexores",
    "chisq_madhu_2025_jexopipe",
    "chisq_hu_2026",
]

REDUCED_SUFFIX = "_reduced"

ObservedSpectrum = tuple[np.ndarray, np.ndarray, np.ndarray]  # (wave_um, depth_ppm, unc_ppm)


# ---------------------------------------------------------------------------
# Per-file-format loaders
# ---------------------------------------------------------------------------

def _load_whitespace_4col(path: Path) -> pd.DataFrame:
    """madhu_2023 / madhu_2025 format: one descriptive (non-data) header
    line, then whitespace-separated wave_um, halfwidth_um, depth, unc --
    depth/unc are fractional (Rp/Rs)^2, NOT ppm."""
    return pd.read_csv(
        path, sep=r"\s+", skiprows=1, header=None,
        names=["wave_um", "halfwidth_um", "depth", "unc"],
    )


def _load_ecsv(path: Path) -> pd.DataFrame:
    """hu_2026 niriss_nameless .spec format: ECSV, '#'-prefixed metadata
    block, comma-delimited data with a real header row. yval/yerr* are
    already in ppm (per the ECSV metadata's declared units)."""
    return pd.read_csv(path, comment="#")


def _load_nirspec_combined(path: Path) -> pd.DataFrame:
    """hu_2026 nirspec_combined .dat format: headerless, whitespace,
    wave_min, wave_max, wave_center, depth, unc -- depth/unc are
    fractional (Rp/Rs)^2, NOT ppm."""
    return pd.read_csv(
        path, sep=r"\s+", header=None,
        names=["wave_min", "wave_max", "wave_center", "depth", "unc"],
    )


# ---------------------------------------------------------------------------
# Per-source assembly (load + trim overlaps + convert to ppm)
# ---------------------------------------------------------------------------

def load_madhu_2023(spectra_dir: Path) -> ObservedSpectrum:
    base = spectra_dir / "madhusudhan_2023"
    niriss = _load_whitespace_4col(base / "K2-18b_niriss_soss_native.txt")
    nirspec = _load_whitespace_4col(base / "K2-18b_nirspec_g395h_native.txt")

    # Trim NIRSpec G395H to avoid double-counting the ~0.084um range where
    # it overlaps NIRISS SOSS's native wavelength coverage (same paper,
    # two instruments -- see module docstring).
    cutoff = niriss["wave_um"].max()
    nirspec = nirspec[nirspec["wave_um"] > cutoff]

    combined = pd.concat([niriss, nirspec], ignore_index=True)
    wave = combined["wave_um"].to_numpy()
    depth_ppm = combined["depth"].to_numpy() * PPM_PER_FRACTION
    unc_ppm = combined["unc"].to_numpy() * PPM_PER_FRACTION
    return wave, depth_ppm, unc_ppm


def load_madhu_2025(spectra_dir: Path, pipeline: str) -> ObservedSpectrum:
    path = spectra_dir / "madhusudhan_2025" / f"K2-18b_miri_lrs_{pipeline}.txt"
    df = _load_whitespace_4col(path)
    wave = df["wave_um"].to_numpy()
    depth_ppm = df["depth"].to_numpy() * PPM_PER_FRACTION
    unc_ppm = df["unc"].to_numpy() * PPM_PER_FRACTION
    return wave, depth_ppm, unc_ppm


def load_hu_2026(spectra_dir: Path) -> ObservedSpectrum:
    niriss_dir = spectra_dir / "hu_2026" / "niriss_nameless"
    order1 = _load_ecsv(niriss_dir / "K2_18b_NIRISS_SOSS_order1_vis1_R300.spec")
    order2 = _load_ecsv(niriss_dir / "K2_18b_NIRISS_SOSS_order2_vis1_R100.spec")

    # order1/order2 are two diffraction orders of the SAME exposure, not
    # independent measurements -- trim order2 below order1's minimum
    # wavelength to avoid double-counting (see module docstring).
    cutoff_niriss = order1["wave"].min()
    order2 = order2[order2["wave"] < cutoff_niriss]

    nirspec_dir = spectra_dir / "hu_2026" / "nirspec_combined"
    g235h = _load_nirspec_combined(nirspec_dir / "K2_G235H_NRS1_NRS2_ave2_direct.dat")
    g395h = _load_nirspec_combined(nirspec_dir / "K2_G395H_NRS1_NRS2_ave3_direct.dat")

    # G235H/G395H "direct" files overlap by ~0.2um (adjacent gratings of
    # the same instrument) -- trim G235H below G395H's minimum. (Only the
    # 2 "direct" files are used at all -- the other 4 files in this
    # directory are alternate reductions of the same data, not extra
    # coverage; see module docstring.)
    cutoff_grism = g395h["wave_center"].min()
    g235h = g235h[g235h["wave_center"] < cutoff_grism]

    # NIRISS (~0.85-2.82um) and NIRSpec G235H (~1.67-3.07um) genuinely
    # overlap (~1.7um) -- NOT trimmed, since they're different
    # instruments/gratings (independent measurements).

    waves, depths_ppm, uncs_ppm = [], [], []
    for df in (order1, order2):
        waves.append(df["wave"].to_numpy())
        depths_ppm.append(df["yval"].to_numpy())
        uncs_ppm.append(((df["yerrLow"] + df["yerrUpp"]) / 2).to_numpy())
    for df in (g235h, g395h):
        waves.append(df["wave_center"].to_numpy())
        depths_ppm.append(df["depth"].to_numpy() * PPM_PER_FRACTION)
        uncs_ppm.append(df["unc"].to_numpy() * PPM_PER_FRACTION)

    return np.concatenate(waves), np.concatenate(depths_ppm), np.concatenate(uncs_ppm)


def load_all_observed(spectra_dir: Path) -> dict[str, ObservedSpectrum]:
    return {
        "chisq_madhu_2023": load_madhu_2023(spectra_dir),
        "chisq_madhu_2025_jexores": load_madhu_2025(spectra_dir, "jexores"),
        "chisq_madhu_2025_jexopipe": load_madhu_2025(spectra_dir, "jexopipe"),
        "chisq_hu_2026": load_hu_2026(spectra_dir),
    }


# ---------------------------------------------------------------------------
# Per-case synthetic spectrum + chi-squared
# ---------------------------------------------------------------------------

def load_case_spectrum(case_dir: Path) -> tuple[np.ndarray, np.ndarray] | None:
    """(wave_um, depth_ppm) sorted by wavelength from a case's offchem
    transit spectrum, or None if missing/unreadable."""
    path = case_dir / "observe" / "transit_offchem_synthesis.csv"
    if not path.is_file():
        print(f"warning: no {path} for {case_dir.name}, skipping spectral fit", file=sys.stderr)
        return None
    try:
        df = pd.read_csv(path, sep=r"\s+")
    except Exception as exc:  # noqa: BLE001 -- report and skip, don't crash the whole run
        print(f"warning: could not read {path}: {exc}, skipping", file=sys.stderr)
        return None
    if df.empty or "Wavelength/um" not in df.columns or "None/ppm" not in df.columns:
        print(f"warning: {path} missing expected columns, skipping", file=sys.stderr)
        return None
    df = df.sort_values("Wavelength/um")
    return df["Wavelength/um"].to_numpy(), df["None/ppm"].to_numpy()


def chi_squared(
    model_wave: np.ndarray, model_depth_ppm: np.ndarray, observed: ObservedSpectrum
) -> float:
    obs_wave, obs_depth_ppm, obs_unc_ppm = observed
    model_interp = np.interp(obs_wave, model_wave, model_depth_ppm)
    return float(np.sum((obs_depth_ppm - model_interp) ** 2 / obs_unc_ppm ** 2))


def compute_all_chisq(
    df: pd.DataFrame, spectra_dir: Path, dest_dir: Path
) -> tuple[pd.DataFrame, dict[str, int], dict[str, int]]:
    observed = load_all_observed(spectra_dir)
    # dof = N (k=0) -- see module docstring: grid points are fixed
    # forward-model evaluations, not fits, so no parameters are
    # subtracted off the point count.
    n_points = {col: obs[0].size for col, obs in observed.items()}

    for col in CHISQ_COLUMNS:
        df[col] = np.nan
        df[col + REDUCED_SUFFIX] = np.nan
    n_fit = {col: 0 for col in CHISQ_COLUMNS}

    for row_idx in df.index:
        case_dir_name = df.at[row_idx, "source_case_dir"]
        if not case_dir_name:
            continue
        spectrum = load_case_spectrum(dest_dir / case_dir_name)
        if spectrum is None:
            continue
        model_wave, model_depth_ppm = spectrum
        for col, obs in observed.items():
            value = chi_squared(model_wave, model_depth_ppm, obs)
            df.at[row_idx, col] = value
            df.at[row_idx, col + REDUCED_SUFFIX] = value / n_points[col]
            n_fit[col] += 1

    return df, n_fit, n_points


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--full-sweep", type=Path, default=hc.DEFAULT_FULL_SWEEP,
        help=f"Full parameter sweep config (default: {hc.DEFAULT_FULL_SWEEP})",
    )
    parser.add_argument(
        "--dest", type=Path, default=hc.DEFAULT_SIMULATION_DATA,
        help=f"Directory of harvested cases to scan (default: {hc.DEFAULT_SIMULATION_DATA})",
    )
    parser.add_argument(
        "--spectra-dir", type=Path, default=DEFAULT_SPECTRA_DIR,
        help=f"Directory of observed K2-18b spectra (default: {DEFAULT_SPECTRA_DIR})",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help=f"Where to write the master CSV (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the summary counts without writing the output file",
    )
    args = parser.parse_args()

    df = bgs.build_summary(args.full_sweep, args.dest)
    total = len(df)
    populated = int((df["source_case_dir"] != "").sum())

    df, n_fit, n_points = compute_all_chisq(df, args.spectra_dir, args.dest)

    print("=" * 70)
    n_success = int((df["source_outcome"] == "success").sum())
    n_vulcan_crashed = int((df["source_outcome"] == "vulcan_crashed").sum())
    print(
        f"Grid summary: {populated} / {total} points populated "
        f"({n_success} success, {n_vulcan_crashed} vulcan_crashed)"
    )
    for col in CHISQ_COLUMNS:
        print(
            f"  {col}: {n_fit[col]} / {populated} fitted "
            f"(N={n_points[col]} obs points, dof=N)"
        )
    print("=" * 70)

    if args.dry_run:
        print("--dry-run: not writing any file.")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
