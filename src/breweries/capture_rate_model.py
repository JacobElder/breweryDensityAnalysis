"""OBDB coverage-correction model, fit on the four calibration states (NC, MI, CO, OR).

Model: log((obdb_count + 0.5) / (licensee_count + 0.5)) ~ log(population_density) + (1 | state)

Two honest findings drive how this is used, not just the point estimates:

1. Population density has a real but small effect (coef ~0.07-0.08, p<0.01): denser
   counties have higher OBDB capture rates, i.e. OBDB undercounts rural areas more.
2. State identity dominates over density. The fixed-effects model's four state
   intercepts range from -0.42 (NC) to +0.09 (OR) relative to the reference level —
   several times larger than the density gradient across its full observed range.
   A pooled (no state term) model has essentially no explanatory power (R^2=0.008).

Consequence: with only 4 calibration states, county density is NOT a reliable basis
for a national per-county correction on its own — state-level regulatory/market
factors this project hasn't measured explain most of the variation, and 4 states is
too few to fit a state-level covariate model. For states with their own calibration
data (NC, MI, CO, OR), use the state-specific empirical capture rate directly. For
all other states, this module returns the pooled cross-state average capture rate
(81.8%) with a wide interval derived from the between-state random-effect variance
— explicitly wide, because that's what an n=4 group sample actually supports.
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

# From the MixedLM fit in scripts/build_capture_rate_model.py.
POOLED_CAPTURE_RATE = 0.818
LOG_DENSITY_COEF = 0.074  # per unit increase in log(people per sq mi)
BETWEEN_STATE_LOG_SD = np.sqrt(0.043)  # ~0.207, REML group-variance estimate, 4 groups


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

    ci_low = np.exp(log_rate - 1.96 * BETWEEN_STATE_LOG_SD)
    ci_high = np.exp(log_rate + 1.96 * BETWEEN_STATE_LOG_SD)
    return {
        "capture_rate": float(np.exp(log_rate)),
        "source": "pooled_extrapolation",
        "ci_low": float(min(ci_low, 1.0)),
        "ci_high": float(min(ci_high, 1.5)),
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
