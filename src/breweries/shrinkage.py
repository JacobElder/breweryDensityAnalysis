"""Empirical Bayes Poisson-Gamma shrinkage via the Clayton-Kaldor method-of-moments
estimator (standard in disease-mapping / small-area rate smoothing).

Chosen over MLE (statsmodels' NegativeBinomial) after the MLE fit was found to be
numerically unstable at the place level: with exposures (adults_21plus) ranging
from a few hundred to millions across ~32,000 places dominated by zero counts, the
NB log-likelihood surface has a degenerate optimum near alpha=0 that collapses
every county's shrunken estimate to the identical flat national mean regardless of
its own count — i.e. silently 100% shrinkage. The method-of-moments estimator
below is the standard, numerically robust alternative for exactly this setting.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


def fit_poisson_gamma(counts: np.ndarray, exposures: np.ndarray) -> tuple[float, float]:
    """Method-of-moments fit of a Gamma(shape, rate) prior on the Poisson rate,
    given observed counts and exposures (e.g. adults_21plus). Returns (shape, rate).
    """
    y = np.asarray(counts, dtype=float)
    n = np.asarray(exposures, dtype=float)
    if np.any(n <= 0):
        raise ValueError(
            "fit_poisson_gamma: exposures must be strictly positive "
            f"({int((n <= 0).sum())} of {len(n)} rows have exposure <= 0); "
            "y/n is undefined for zero exposure. Filter these rows before fitting."
        )
    mu = y.sum() / n.sum()

    N = len(y)
    if N < 2:
        raise ValueError("fit_poisson_gamma: need at least 2 units to estimate between-unit variance.")
    numerator = (n * (y / n - mu) ** 2).sum() - (N - 1) * mu
    denominator = n.sum() - (n ** 2).sum() / n.sum()
    sigma2 = numerator / denominator

    if sigma2 <= 0:
        # No detectable overdispersion beyond Poisson sampling noise at this
        # geographic level — fall back to a very large shape (near-zero shrinkage
        # variance) rather than a negative/undefined Gamma.
        sigma2 = mu ** 2 / 1e6

    shape = mu ** 2 / sigma2
    rate = mu / sigma2
    return shape, rate


def shrink_rates(df: pd.DataFrame, count_col: str, exposure_col: str) -> pd.DataFrame:
    """Add posterior-mean-rate columns (and a 95% credible interval) via empirical
    Bayes Poisson-Gamma shrinkage, partial-pooling toward the population-weighted
    national mean rate.
    """
    df = df.copy()
    shape, rate = fit_poisson_gamma(df[count_col].values, df[exposure_col].values)

    post_shape = shape + df[count_col]
    post_rate = rate + df[exposure_col]
    df["eb_posterior_rate"] = post_shape / post_rate
    df["eb_posterior_rate_per_100k"] = df["eb_posterior_rate"] * 100_000

    # Exact Gamma(post_shape, post_rate) quantiles rather than a normal
    # approximation. The normal approximation is a poor fit here: the
    # national-level prior shape is typically well below 1 (e.g. ~1.0 at the
    # county level and ~0.36 at the place level in this project's data), and
    # posterior shape = prior shape + count, so the majority of units — every
    # county/place with 0 or 1 observed breweries, which is most of them —
    # have a strongly right-skewed posterior (skewness = 2/sqrt(shape)).
    # A symmetric normal CI on that distribution clips the lower bound to 0
    # (losing information) and understates the upper bound by 20-30%+ in
    # spot checks. scipy.stats.gamma gives the exact interval at negligible
    # extra cost.
    ci_low = stats.gamma.ppf(0.025, a=post_shape, scale=1 / post_rate)
    ci_high = stats.gamma.ppf(0.975, a=post_shape, scale=1 / post_rate)
    df["eb_ci_low_per_100k"] = np.maximum(0, ci_low * 100_000)
    df["eb_ci_high_per_100k"] = ci_high * 100_000

    df.attrs["gamma_shape"] = shape
    df.attrs["gamma_rate"] = rate
    return df
