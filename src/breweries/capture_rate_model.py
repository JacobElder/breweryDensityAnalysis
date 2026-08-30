"""OBDB coverage-correction model, fit on 13 calibration states
(NC, MI, CO, OR, WA, TX, GA, WI, PA, IL, CA, NY, VA).

Model: log((obdb_count + 0.5) / (licensee_count + 0.5)) ~ log(population_density),
weighted by licensee_count (WLS), fit in scripts/build_capture_rate_model.py.

Three honest findings drive how this is used, not just the point estimates:

1. Population density has a real but small effect (coef ~0.066, p<0.001): denser
   counties have higher OBDB capture rates, i.e. OBDB undercounts rural areas more.
2. State identity dominates over density. A fixed-effects model's state
   intercepts vary far more than the density gradient across its full observed
   range, and a model with no state term has essentially no explanatory power.
3. The exposure-weighted *aggregate* ratio across all pooled counties is a
   different quantity from "the capture rate of a typical county" — the
   aggregate is pulled up by a handful of large, high-capture counties.
   POOLED_CAPTURE_RATE below is deliberately the WLS-regression-implied value,
   not the aggregate, because correction_factor() applies its fallback to
   arbitrary counties nationally, most of which are small/medium, not large
   metros — using the aggregate ratio would systematically under-correct
   exactly the smaller/rural counties this correction is supposed to help.

Several calibration states' licensee registries measure a different
population than "OBDB-listed craft breweries," which shows up as a raw
capture rate at or above 100% (kept in the model rather than dropped without
a principled statistical reason — but flagged clearly):

- **Wisconsin** (116.6%): WI DOR's "Brewery" permit type sweeps in some
  non-craft manufacturers (e.g. Anheuser-Busch's Milwaukee plant).
- **Texas** (122.2%): TABC's public license table is documented (by TABC's
  own license-consolidation materials) to exclude brewpub subordinate
  authorizations attached to a retail permit — the reference undercounts,
  not evidence OBDB over-counts.
- **California** (135.3%): ABC's export counts *licenses*, and many brands
  hold multiple CA licenses (satellite tasting rooms, alternating
  proprietorships), plus some large non-craft manufacturers hold a
  beer-manufacturer license incidentally (e.g. large wineries).
- **Virginia** (120.1%): ABC's export similarly counts licensed *premises*;
  several brands hold multiple Virginia sites (e.g. one operator with 5).
- **Illinois** (108.6%): ILCC's export is cumulative (active status inferred
  from expiration date, no explicit status column) and companion license
  classes (a base "Brewer" license plus a production-tier overlay) can
  double-list one physical site despite deduplication.

A capture rate is a fraction of a true population and cannot exceed 1.0 by
definition, so correction_factor() clips every rate (calibrated or pooled) at
1.0 — otherwise apply_correction() would divide by >1 and produce a
"corrected" estimate *lower* than the raw OBDB count, inverting the entire
purpose of the correction.

Consequence: county density is NOT a reliable basis for a national per-county
correction on its own — state-level regulatory/market factors this project
hasn't measured explain most of the variation, and 13 states is still not a
lot for a state-level covariate model. For states with their own calibration
data, use the state-specific empirical capture rate directly. For all other
states, this module returns the WLS-regression pooled rate with a wide
interval derived from the between-state random-effect variance — explicitly
wide, because that's what a 13-group sample actually supports.

States investigated and confirmed to have no bulk open-data source (only an
interactive per-record search tool this project's rules forbid scripting
around) and are NOT calibration states: MS, OH, VT, MN. TN, AZ, and SC also
lack a state licensee source but do have OBDB/OSM/CBP-only county datasets
(`build_{state}_county_dataset.py`) used for face-validity checks elsewhere,
not for this model.
"""

from __future__ import annotations

import numpy as np

# Empirical OBDB capture rate (obdb_count / licensee_count, pooled across counties)
# in each calibration state, from build_{state}_county_dataset.py. States above
# 1.0 are left unclipped here so the raw number is visible/auditable; clipping
# to <=1.0 happens uniformly in correction_factor() for every state, calibrated
# or pooled — see the module docstring for why each one exceeds 1.0.
CALIBRATED_STATE_CAPTURE_RATES = {
    "NC": 0.618,
    "MI": 0.846,
    "CO": 0.919,
    "OR": 0.930,
    "WA": 0.830,
    "TX": 1.222,
    "GA": 0.476,
    "WI": 0.615,
    "PA": 0.486,
    "IL": 1.086,
    "CA": 0.600,
    "NY": 0.665,
    "VA": 0.465,
}

# From the WLS fit (weights=licensee_count) in scripts/build_capture_rate_model.py —
# both drawn from the SAME model so the baseline and the density adjustment are
# internally consistent (see module docstring point 3 for why this isn't just the
# raw aggregate ratio).
POOLED_CAPTURE_RATE = 0.593  # WLS intercept prediction at mean log_density
LOG_DENSITY_COEF = 0.066  # WLS slope, per unit increase in log(people per sq mi)
BETWEEN_STATE_LOG_SD = np.sqrt(0.0897)  # ~0.300, REML group-variance estimate, 13 groups (unweighted MixedLM; see build script)


def correction_factor(state: str, log_density: float | None = None) -> dict:
    """Return a capture-rate estimate (and how much to trust it) for a state/county.

    For a calibrated state, returns its empirical rate with no extrapolation
    uncertainty. For any other state, returns the pooled rate with a 95% interval
    wide enough to reflect that it's estimated from only 13 groups — do not read
    the interval bounds as precise; they exist to keep downstream users from
    treating a single national number as more certain than it is.
    """
    if state in CALIBRATED_STATE_CAPTURE_RATES:
        # min(..., 1.0): a capture rate is a fraction of a true population and
        # cannot exceed 1.0 by definition. Several states' raw values exceed 1.0
        # (see module docstring — the licensee reference itself over- or
        # under-counts in each case, not evidence OBDB over-counts); clip here
        # so apply_correction() never divides by >1 and inverts the correction
        # direction.
        return {
            "capture_rate": min(CALIBRATED_STATE_CAPTURE_RATES[state], 1.0),
            "source": "calibrated",
            "ci_low": None,
            "ci_high": None,
        }

    log_rate = np.log(POOLED_CAPTURE_RATE)
    if log_density is not None:
        log_rate += LOG_DENSITY_COEF * (log_density - _mean_log_density())

    # A capture rate is a fraction of a true population — it cannot exceed 1.0 by
    # definition, but the log-linear density extrapolation isn't bounded above and
    # does cross 1.0 for the handful of US counties far denser than anything in the
    # calibration sample (Manhattan at ~72k people/sqmi vs. nothing remotely that
    # dense in the 13 calibration states). Clip the point estimate and both CI
    # bounds at 1.0 rather than let "112% of breweries captured" through silently.
    rate = min(float(np.exp(log_rate)), 1.0)
    ci_low = min(np.exp(log_rate - 1.96 * BETWEEN_STATE_LOG_SD), 1.0)
    ci_high = min(np.exp(log_rate + 1.96 * BETWEEN_STATE_LOG_SD), 1.0)
    return {
        "capture_rate": rate,
        "source": "pooled_extrapolation",
        "ci_low": float(ci_low),
        "ci_high": float(ci_high),
    }


def _mean_log_density() -> float:
    # Mean log(density) across the calibration-state counties used to fit the
    # model; centers the density adjustment so the pooled rate applies at the
    # average density rather than at density=1/sqmi.
    return 4.839  # ~126 people/sqmi, from data/processed/pooled_calibration_with_density.parquet


def apply_correction(obdb_count: int, state: str, log_density: float | None = None) -> dict:
    result = correction_factor(state, log_density)
    corrected = obdb_count / result["capture_rate"]
    out = {"obdb_count": obdb_count, "corrected_estimate": corrected, **result}
    if result["ci_low"] is not None:
        out["corrected_low"] = obdb_count / result["ci_high"]
        out["corrected_high"] = obdb_count / result["ci_low"]
    return out
