"""Capture rate as a latent quantity with propagated uncertainty, rather than
a fixed offset.

THE PROBLEM WITH THE FIXED OFFSET
---------------------------------
`fit_combined_spatial_covariate_model.CAPTURE_RATE_OFFSET` puts
log(capture_rate) straight into the model's offset. That de-confounds the
state fixed effect from OBDB's state-level coverage gap, which is real and
worth doing -- Georgia's state effect moves +0.72 when refit on corrected
counts, and log(1/0.476) = 0.74, i.e. essentially all of Georgia's state
effect was coverage rather than beer.

But an offset asserts the capture rate *with zero error*, and it is not known
with zero error:

    calibrated states        1,747 counties, 70.4% of adults 21+
    pooled_extrapolation     1,475 counties, 29.6% of adults 21+

For that second group the rate is a WLS extrapolation whose own 95% interval
spans a factor of ~3.6 (BETWEEN_STATE_LOG_SD ~ 0.326 on the log scale, from
only 23 calibration groups). Hard-coding it would present an extrapolation as
fact for nearly a third of the US population and return posterior intervals
that are far too narrow.

That understatement is worse now than it used to be, because the headline
choropleth fades each county by its interval width. Too-narrow intervals no
longer just shrink a number in a table -- they make a county look MORE
confidently drawn on the map. And since the least-certain capture rates are
exactly the uncalibrated states, a fixed offset would render the worst-covered
states as the most confident ones. Precisely backwards.

WHAT THE DATA CAN AND CANNOT DO
-------------------------------
Write the model out:

    log E[observed_i] = log(adults_i) + log(c_s) + X_i.beta + spatial_i

where `c_s` is state s's capture rate and X already contains a full-rank state
dummy. `log(c_s)` and the state dummy's coefficient are *additively
confounded*: the likelihood only ever sees their sum. No amount of brewery
count data can separate them, because nothing in the counts distinguishes "few
breweries here" from "few breweries listed here".

So it is worth being blunt about what a "latent capture rate" model does: the
separation is driven entirely by the external calibration prior, not learned
from the data. The useful consequence is that the prior's uncertainty then
propagates into the true-rate intervals, which is the whole point.

This also means the sensible parameterization is the orthogonal one:

    state_total_s = beta_state_s + log(c_s)     <- identified by the data
    log(c_s)                                    <- fixed by the prior

    observed rate_i = exp(X_cov.beta + state_total_s + spatial_i)
    true rate_i     = observed rate_i / c_s

Fitting `state_total` directly is exactly the model already fitted (the
likelihood is unchanged, and the implied prior on state_total widens only from
sd 2.0 to sqrt(2.0^2 + 0.326^2) = 2.026). So the true-scale rates can be
obtained from the EXISTING production posterior by dividing by draws from the
capture-rate prior -- no second MCMC run, and no pretence that the counts
informed the capture rate. `scripts/fit_latent_capture_rate_model.py`
validates that equivalence against a direct joint fit rather than asserting it.

A consequence worth stating plainly: because the observed-scale posterior is
untouched, the headline map does not move. Only the true-scale quantity gains
the extra uncertainty it always should have had.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from breweries.capture_rate_model import (
    BETWEEN_STATE_LOG_SD,
    CALIBRATED_STATE_CAPTURE_RATES,
    correction_factor,
)

# Log-scale sd applied to a CALIBRATED state's capture rate.
#
# capture_rate_model.correction_factor() returns ci_low=ci_high=None for
# calibrated states, i.e. treats the measured value as exact. It isn't: the
# rate is a ratio of two counts produced by matching OBDB against a state
# licensee registry, so it carries matching error, registry-currency error and
# license-category error. The module's own docstring documents six states whose
# raw ratio lands ABOVE 1.0 for exactly those reasons (WY 1.286, MO 1.662,
# TX 1.222) and are then clipped -- direct evidence that these numbers are not
# exact.
#
# 0.10 (~10% relative) is a deliberately modest allowance: large enough that
# calibrated states are not treated as noiseless, small enough that they stay
# clearly better-determined than the pooled ones (whose sd is 0.326, ~3x
# wider). It is a documented sensitivity knob, not an estimate -- there is no
# repeated-measurement design here that could estimate it.
CALIBRATED_LOG_SD = 0.10

# A capture rate is a share of a true population, so c <= 1 and log(c) <= 0.
LOG_CAPTURE_UPPER = 0.0


def state_capture_priors(
    states: list[str], mean_log_density_by_state: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Per-state prior on log(capture rate): mean, sd, and provenance.

    Returns columns: state_abbr, mu_log_c, sd_log_c, source.

    Calibrated states take their measured rate with CALIBRATED_LOG_SD.
    Everything else takes the pooled WLS rate with the between-state log sd,
    which is what a 23-group sample supports. `mean_log_density_by_state`
    optionally supplies the density used by the pooled model's density
    adjustment; omit it to use the pooled rate at mean density.
    """
    rows = []
    for state in states:
        log_density = (mean_log_density_by_state or {}).get(state)
        cf = correction_factor(state, log_density=log_density)
        rate = float(np.clip(cf["capture_rate"], 1e-3, 1.0))
        if state in CALIBRATED_STATE_CAPTURE_RATES:
            sd = CALIBRATED_LOG_SD
        else:
            sd = float(BETWEEN_STATE_LOG_SD)
        rows.append({"state_abbr": state, "mu_log_c": float(np.log(rate)),
                     "sd_log_c": sd, "source": cf["source"]})
    return pd.DataFrame(rows)


def sample_log_capture(
    priors: pd.DataFrame, n_draws: int, seed: int = 42,
) -> tuple[np.ndarray, list[str]]:
    """Draw (n_draws, n_states) from the per-state truncated-normal prior on
    log(capture rate), truncated above at LOG_CAPTURE_UPPER so c <= 1.

    Truncation matters most for the states whose measured ratio was clipped to
    exactly 1.0: without it their prior would put half its mass on "OBDB lists
    more breweries than exist", which is not a coherent capture rate.
    """
    rng = np.random.default_rng(seed)
    mu = priors["mu_log_c"].to_numpy(dtype=float)
    sd = priors["sd_log_c"].to_numpy(dtype=float)
    a = -np.inf * np.ones_like(mu)
    b = (LOG_CAPTURE_UPPER - mu) / sd
    draws = stats.truncnorm.rvs(
        a, b, loc=mu, scale=sd, size=(n_draws, len(mu)), random_state=rng)
    return draws, priors["state_abbr"].tolist()


def true_rate_samples(
    observed_rate_samples: np.ndarray, state_index: np.ndarray, priors: pd.DataFrame,
    seed: int = 42,
) -> np.ndarray:
    """Convert observed-scale rate draws to TRUE-scale rate draws.

    observed_rate_samples: (n_draws, n_counties), the existing posterior.
    state_index: (n_counties,) index into `priors` rows.

    true_rate = observed_rate / c_state, with one independent capture draw per
    posterior draw, so the capture prior's spread is convolved into the result
    rather than applied as a point scaling. Applying the point estimate
    instead -- the fixed-offset approach -- shifts the interval without
    widening it, which is exactly the understatement this module exists to
    avoid.
    """
    n_draws = observed_rate_samples.shape[0]
    log_c, _ = sample_log_capture(priors, n_draws, seed=seed)
    return observed_rate_samples / np.exp(log_c[:, state_index])


def summarize(samples: np.ndarray) -> pd.DataFrame:
    """Posterior median and 95% interval per county, matching the convention
    used for the observed-scale rates (median, not mean -- see
    fit_combined_spatial_covariate_model for why)."""
    return pd.DataFrame({
        "median": np.percentile(samples, 50, axis=0),
        "ci_low": np.percentile(samples, 2.5, axis=0),
        "ci_high": np.percentile(samples, 97.5, axis=0),
    })
