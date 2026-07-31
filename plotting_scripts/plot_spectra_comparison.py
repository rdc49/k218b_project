#!/usr/bin/env python3
"""Plot a 3x3 grid of synthetic transmission spectra against the observed
Madhusudhan et al. 2023 K2-18 b spectrum, spanning the full range of
chi-squared fit quality.

Why this exists
-----------------------------------------------------------------------
simulation_data/grid_master.csv has a chisq_madhu_2023_reduced column (see
scripts/compute_spectral_chisq.py) scoring every successful grid point's
synthetic spectrum against the real observed spectrum, but nothing plots
the spectra themselves. This picks 9 cases spanning best-fit to worst-fit
and overlays each synthetic spectrum against the real data, so the
chi-squared numbers can be checked against what the spectra actually look
like.

Case selection: cases with a non-NaN chisq_madhu_2023_reduced are sorted
ascending (best fit first), then 9 evenly-spaced RANK positions are chosen
(np.linspace over the sorted index, not over the chisq value itself) --
this guarantees a genuine spread across the whole quality range regardless
of how skewed the chisq distribution is.

Observed data: the LOWRES Madhusudhan et al. 2023 files (not the native
files compute_spectral_chisq.py fits against) -- lowres has ~55-57 points
per instrument, giving legible error bars at 3x3 subplot scale. Native
would be a dense, unreadable error-bar smear at this size. NIRSpec G395H is
trimmed to wavelengths above NIRISS SOSS's max, mirroring the overlap trim
compute_spectral_chisq.py.load_madhu_2023 applies to the native files, for
the same reason (same paper, two instruments, avoid double-plotting the
~0.084um overlap).

Usage:
    python3 plotting_scripts/plot_spectra_comparison.py
    python3 plotting_scripts/plot_spectra_comparison.py --n 9 --output out.pdf
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_CSV = PROJECT_ROOT / "simulation_data" / "grid_master.csv"
DEFAULT_SIMULATION_DATA = PROJECT_ROOT / "simulation_data"
DEFAULT_SPECTRA_DIR = PROJECT_ROOT / "k218b_spectra" / "madhusudhan_2023"
DEFAULT_OUTPUT = PROJECT_ROOT / "paper" / "Figures" / "spectra_comparison" / "madhu_2023_spectra_grid.pdf"

CHISQ_COL = "chisq_madhu_2023_reduced"
PPM_PER_FRACTION = 1e6

MODEL_COLOR = "#1b1b1b"
OBS_COLOR = "#d55e00"  # colourblind-safe orange (Okabe-Ito)

PARAM_COLS = {
    "fO2": "fO2_shift_IW",
    "H": "H_budget",
    "C": "C_budget",
    "N": "N_budget",
    "S": "S_budget",
}


def load_whitespace_4col(path: Path) -> pd.DataFrame:
    """madhu_2023 format: one descriptive header line, then whitespace-
    separated wave_um, halfwidth_um, depth, unc -- depth/unc are fractional
    (Rp/Rs)^2, NOT ppm."""
    return pd.read_csv(
        path, sep=r"\s+", skiprows=1, header=None,
        names=["wave_um", "halfwidth_um", "depth", "unc"],
    )


def load_observed_lowres(spectra_dir: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    niriss = load_whitespace_4col(spectra_dir / "K2-18b_niriss_soss_lowres.txt")
    nirspec = load_whitespace_4col(spectra_dir / "K2-18b_nirspec_g395h_lowres.txt")

    # Trim NIRSpec G395H to avoid double-plotting the ~0.084um overlap with
    # NIRISS SOSS (same paper, two instruments -- mirrors the trim
    # compute_spectral_chisq.py applies to the native files).
    cutoff = niriss["wave_um"].max()
    nirspec = nirspec[nirspec["wave_um"] > cutoff]

    combined = pd.concat([niriss, nirspec], ignore_index=True).sort_values("wave_um")
    wave = combined["wave_um"].to_numpy()
    depth_ppm = combined["depth"].to_numpy() * PPM_PER_FRACTION
    unc_ppm = combined["unc"].to_numpy() * PPM_PER_FRACTION
    return wave, depth_ppm, unc_ppm


def load_case_spectrum(case_dir: Path) -> tuple[np.ndarray, np.ndarray]:
    path = case_dir / "observe" / "transit_offchem_synthesis.csv"
    df = pd.read_csv(path, sep=r"\s+")
    df = df.sort_values("Wavelength/um")
    return df["Wavelength/um"].to_numpy(), df["None/ppm"].to_numpy()


def select_cases(df: pd.DataFrame, n: int) -> pd.DataFrame:
    fit = df[df[CHISQ_COL].notna()].sort_values(CHISQ_COL).reset_index(drop=True)
    if fit.empty:
        raise SystemExit(f"No rows with a non-NaN {CHISQ_COL} found -- nothing to plot.")
    n_available = len(fit)
    n_pick = min(n, n_available)
    positions = np.unique(np.round(np.linspace(0, n_available - 1, n_pick)).astype(int))
    selected = fit.loc[positions].copy()
    selected["_rank"] = positions + 1
    selected["_n_total"] = n_available
    return selected


def format_param_line(row: pd.Series) -> str:
    return (
        f"fO2={row[PARAM_COLS['fO2']]:g}, H={row[PARAM_COLS['H']]:.0f} ppmw\n"
        f"C/H={row[PARAM_COLS['C']]:.3g}, N/H={row[PARAM_COLS['N']]:.3g}, "
        f"S/H={row[PARAM_COLS['S']]:.3g}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Path to grid_master.csv")
    parser.add_argument(
        "--simulation-data", type=Path, default=DEFAULT_SIMULATION_DATA,
        help="Directory of harvested cases (default: simulation_data/)",
    )
    parser.add_argument(
        "--spectra-dir", type=Path, default=DEFAULT_SPECTRA_DIR,
        help="Directory of observed madhusudhan_2023 spectrum files",
    )
    parser.add_argument("--n", type=int, default=9, help="Number of cases to plot (default: 9)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output PDF path")
    args = parser.parse_args()

    if not args.csv.is_file():
        raise SystemExit(f"CSV not found: {args.csv}")
    df = pd.read_csv(args.csv, dtype={"source_case_number": str})
    if CHISQ_COL not in df.columns:
        raise SystemExit(f"{args.csv} has no {CHISQ_COL} column -- run compute_spectral_chisq.py first.")

    selected = select_cases(df, args.n)
    obs_wave, obs_depth_ppm, obs_unc_ppm = load_observed_lowres(args.spectra_dir)

    n_plots = len(selected)
    ncols = 3
    nrows = int(np.ceil(n_plots / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(15, 12), constrained_layout=True, squeeze=False)

    xlim = (obs_wave.min() - 0.1, obs_wave.max() + 0.1)
    model_line = obs_line = None

    for i, (_, row) in enumerate(selected.iterrows()):
        ax = axes[i // ncols][i % ncols]
        case_dir = args.simulation_data / row["source_case_dir"]
        model_wave, model_depth_ppm = load_case_spectrum(case_dir)

        (model_line,) = ax.plot(
            model_wave, model_depth_ppm, "-", color=MODEL_COLOR, lw=1.2, zorder=2,
        )
        obs_line = ax.errorbar(
            obs_wave, obs_depth_ppm, yerr=obs_unc_ppm,
            fmt="o", color=OBS_COLOR, markersize=3, elinewidth=1, capsize=2, zorder=3,
        )

        ax.set_xlim(*xlim)
        ax.set_title(
            f"{format_param_line(row)}\n"
            rf"$\chi^2_\nu$={row[CHISQ_COL]:.3g}  (rank {row['_rank']}/{row['_n_total']})",
            fontsize=9,
        )

        if i // ncols == nrows - 1:
            ax.set_xlabel("Wavelength [$\\mu$m]")
        if i % ncols == 0:
            ax.set_ylabel("Transit depth [ppm]")

    # Hide any unused trailing subplots (only relevant if n < nrows*ncols).
    for j in range(n_plots, nrows * ncols):
        axes[j // ncols][j % ncols].set_visible(False)

    fig.legend(
        [model_line, obs_line],
        ["Synthetic spectrum", "Madhusudhan et al. 2023 (observed)"],
        loc="outside upper center", ncol=2, fontsize=10,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output)
    plt.close(fig)
    print(f"Saved: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
