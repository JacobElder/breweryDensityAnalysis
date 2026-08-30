"""OBDB coverage-correction model, fit on the four calibration states (NC, MI, CO, OR).

Model: log((obdb_count + 0.5) / (licensee_count + 0.5)) ~ log(population_density),
weighted by licensee_count (WLS), fit in scripts/build_capture_rate_model.py.

Three honest findings drive how this is used, not just the point estimates:

1. Population density has a real but small effect (coef ~0.076, p<0.001): denser
   counties have higher OBDB capture rates, i.e. OBDB undercounts rural areas more.
2. State identity dominates over density. A fixed-effects model's four state
   intercepts range over roughly 4x the magnitude of the density gradient across
   its full observed range, and a model with no state term has essentially no
   explanatory power (R^2=0.09 even including density).
3. The exposure-weighted *aggregate* ratio across all 216 calibration counties
   (obdb_count.sum()/licensee_count.sum() = 81.8%) is a different quantity from
   "the capture rate of a typical county" (~70.5%) — the aggregate is pulled up by
   a handful of large, high-capture counties (Buncombe, Mecklenburg, Wake, Denver).
   POOLED_CAPTURE_RATE below is deliberately the latter (WLS-regression-implied),
   not the former, because correction_factor() applies its fallback to arbitrary
   counties nationally, most of which are small/medium, not large metros — using
   the aggregate ratio would systematically under-correct exactly the
   smaller/rural counties this correction is supposed to help.

Consequence: with only 4 calibration states, county density is NOT a reliable basis
for a national per-county correction on its own — state-level regulatory/market
factors this project hasn't measured explain most of the variation, and 4 states is
too few to fit a state-level covariate model. For states with their own calibration
data (NC, MI, CO, OR), use the state-specific empirical capture rate directly. For
all other states, this module returns the WLS-regression pooled rate with a wide
interval derived from the between-state random-effect variance — explicitly wide,
because that's what an n=4 group sample actually supports.
"""

from __future__ import annotations

import numpy as np

# Empirical OBDB capture rate (obdb_count / licensee_count, pooled across counties)
# in each calibration state, from build_{nc,mi,co,or}_county_dataset.py.
CALIBRATED_STATE_CAPTURE_RATES = {
    "NC": 0.618,
    "MI": 0.846,
    "CO": 0.919,
    "OR": 0.930,
}

# From the WLS fit (weights=licensee_count) in scripts/build_capture_rate_model.py —
# both drawn from the SAME model so the baseline and the density adjustment are
# internally consistent (see module docstring point 3 for why this isn't just the
# raw aggregate ratio).
POOLED_CAPTURE_RATE = 0.705  # WLS intercept prediction at mean log_density
LOG_DENSITY_COEF = 0.076  # WLS slope, per unit increase in log(people per sq mi)
BETWEEN_STATE_LOG_SD = np.sqrt(0.043)  # ~0.207, REML group-variance estimate, 4 groups (unweighted MixedLM; see build script)


def correction_factor(state: str, log_density: float | None = None) -> dict:
    """Return a capture-rate estimate (and how much to trust it) for a state/county.

    For a calibrated state, returns its empirical rate with no extrapolation
    uncertainty. For any other state, returns the pooled rate with a 95% interval
    wide enough to reflect that it's estimated from only 4 groups — do not read the
    interval bounds as precise; they exist to keep downstream users from treating a
    single national number as more certain than it is.
    """
    if state in CALIBRATED_STATE_CAPTURE_RATES:
        return {
            "capture_rate": CALIBRATED_STATE_CAPTURE_RATES[state],
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
    # 4-state calibration sample (Manhattan at ~72k people/sqmi vs. nothing
    # remotely that dense in NC/MI/CO/OR). Clip the point estimate and both CI
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
    # Mean log(density) across the 216 calibration-state counties used to fit the
    # model; centers the density adjustment so the pooled rate applies at the
    # average density rather than at density=1/sqmi.
    return 4.335  # ~76 people/sqmi, from data/processed/pooled_calibration_with_density.csv


def apply_correction(obdb_count: int, state: str, log_density: float | None = None) -> dict:
    result = correction_factor(state, log_density)
    corrected = obdb_count / result["capture_rate"]
    out = {"obdb_count": obdb_count, "corrected_estimate": corrected, **result}
    if result["ci_low"] is not None:
        out["corrected_low"] = obdb_count / result["ci_high"]
        out["corrected_high"] = obdb_count / result["ci_low"]
    return out
