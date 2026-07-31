---
name: proteus-config-review
description: Use whenever writing, reviewing, or debugging a PROTEUS TOML config for this project (k218b_fiducial.toml, a grid_sweep_configs/*.toml, or a one-off gapfill config) — a checklist of PROTEUS config gotchas learned the hard way, e.g. module-conditional subtables being silently inert, instellation scaling, star age anchoring, unvalidated solubility strings, real-gas EOS switches, and core_frac_mode.
---

# PROTEUS TOML config gotchas

Learned from reviewing this project's fiducial config
(`k218b_fiducial.toml`); these generalise beyond that one file — useful
whenever writing or reviewing any PROTEUS TOML for this project.

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
  non-ideal-gas behaviour matters for a given run. (This project's fiducial
  config leaves `eos_*` unset, i.e. ideal gas, while `real_gas = true` for
  AGNI's atmosphere structure only.)
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

For this project's actual module selections and fixed system parameters,
see the "Fiducial configuration" section of `CLAUDE.md` — treat
`k218b_fiducial.toml`, not the generic PROTEUS description in
`proteus_CLAUDE.md`, as the source of truth for which modules are in use.
Every config must also set an explicit `planet.elements.O_mode`
(`"ic_chemistry"`, `"ppmw"`, `"kg"`, or `"FeO_mantle_wt_pct"`) — this
directly interacts with the fO2 sweep axis, so pick the mode deliberately
and keep it consistent across the grid so fO2 comparisons stay
apples-to-apples. The H/C/N/S volatile budgets are set the same way
(`ppmw`/`kg` per element). Before launching any run built from this config,
also see the `launch-grid-batch` skill's fiducial-config drift check.
