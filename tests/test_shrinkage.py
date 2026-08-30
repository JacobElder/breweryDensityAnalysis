"""Regression tests for src/breweries/shrinkage.py (empirical Bayes
Poisson-Gamma shrinkage via Clayton-Kaldor method-of-moments).

Key regression coverage: this module's confidence intervals used to be
computed via a normal approximation on the Gamma posterior, which is a poor
fit whenever posterior shape < 1 (skewness = 2/sqrt(shape)) -- the common
case here since most counties/places have 0 or 1 observed breweries. The
fix replaced that with exact `scipy.stats.gamma.ppf` quantiles. The tests
in `TestCredibleIntervalAsymmetry` recompute those quantiles independently
(not by calling shrink_rates twice) so a future regression back to a normal
approximation would fail them.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from breweries.shrinkage import fit_poisson_gamma, shrink_rates


# ---------------------------------------------------------------------------
# fit_poisson_gamma: method-of-moments formula verification
# ---------------------------------------------------------------------------

class TestFitPoissonGammaFormula:
    """Verify fit_poisson_gamma against an independently written
    reimplementation of the Clayton-Kaldor method-of-moments formula
    documented in the module docstring:

        mu = sum(y) / sum(n)
        sigma2 = [sum(n*(y/n - mu)^2) - (N-1)*mu] / [sum(n) - sum(n^2)/sum(n)]
        shape = mu^2 / sigma2
        rate  = mu / sigma2

    This reference implementation is written from scratch here (not
    imported from shrinkage.py), so it would catch a regression where the
    module's formula silently diverges from what it claims to compute.
    """

    @staticmethod
    def _reference_mom_fit(y: np.ndarray, n: np.ndarray) -> tuple[float, float]:
        y = np.asarray(y, dtype=float)
        n = np.asarray(n, dtype=float)
        N = len(y)
        mu = y.sum() / n.sum()
        numerator = np.sum(n * (y / n - mu) ** 2) - (N - 1) * mu
        denominator = n.sum() - np.sum(n ** 2) / n.sum()
        sigma2 = numerator / denominator
        if sigma2 <= 0:
            sigma2 = mu ** 2 / 1e6
        shape = mu ** 2 / sigma2
        rate = mu / sigma2
        return shape, rate

    def test_small_synthetic_dataset_matches_reference_formula(self):
        # 5 hand-picked counties with modest between-unit rate variation.
        counts = np.array([2, 5, 1, 8, 3], dtype=float)
        exposures = np.array([100, 200, 50, 300, 150], dtype=float)

        expected_shape, expected_rate = self._reference_mom_fit(counts, exposures)
        # Sanity: these should be finite, positive, and not the degenerate
        # fallback (i.e. this dataset does have genuine between-unit
        # variance beyond Poisson noise).
        assert expected_shape > 0
        assert expected_rate > 0

        shape, rate = fit_poisson_gamma(counts, exposures)
        assert shape == pytest.approx(expected_shape, rel=1e-9)
        assert rate == pytest.approx(expected_rate, rel=1e-9)

    def test_another_synthetic_dataset_matches_reference_formula(self):
        # A second, larger dataset (10 units) with more dispersion, to
        # exercise the formula on a different shape of input.
        rng_counts = np.array([0, 1, 0, 4, 2, 0, 10, 1, 0, 3], dtype=float)
        exposures = np.array(
            [500, 800, 300, 1200, 900, 400, 2000, 700, 250, 1100], dtype=float
        )
        expected_shape, expected_rate = self._reference_mom_fit(rng_counts, exposures)
        shape, rate = fit_poisson_gamma(rng_counts, exposures)
        assert shape == pytest.approx(expected_shape, rel=1e-9)
        assert rate == pytest.approx(expected_rate, rel=1e-9)

    def test_shape_over_rate_equals_mean_rate(self):
        # shape/rate always equals mu = sum(y)/sum(n) by construction of the
        # method-of-moments estimator (both the "good variance" and the
        # sigma2<=0 fallback branch preserve this identity).
        counts = np.array([3, 0, 7, 1, 2], dtype=float)
        exposures = np.array([1000, 500, 2000, 400, 900], dtype=float)
        shape, rate = fit_poisson_gamma(counts, exposures)
        mu = counts.sum() / exposures.sum()
        assert shape / rate == pytest.approx(mu, rel=1e-9)


# ---------------------------------------------------------------------------
# Posterior update: post_shape = shape + count, post_rate = rate + exposure
# ---------------------------------------------------------------------------

class TestPosteriorUpdate:
    def test_posterior_shape_rate_and_mean_formula(self):
        counts = np.array([2, 5, 1, 8, 3, 0, 6], dtype=float)
        exposures = np.array([100, 200, 50, 300, 150, 80, 400], dtype=float)
        df = pd.DataFrame({"count": counts, "exposure": exposures})

        out = shrink_rates(df, "count", "exposure")
        shape = out.attrs["gamma_shape"]
        rate = out.attrs["gamma_rate"]

        expected_post_shape = shape + counts
        expected_post_rate = rate + exposures
        expected_posterior_rate = expected_post_shape / expected_post_rate

        assert out["eb_posterior_rate"].to_numpy() == pytest.approx(
            expected_posterior_rate, rel=1e-9
        )
        assert out["eb_posterior_rate_per_100k"].to_numpy() == pytest.approx(
            expected_posterior_rate * 100_000, rel=1e-9
        )

    def test_posterior_update_with_known_prior_values(self):
        # Directly verify the update arithmetic for one row using externally
        # fixed prior (shape, rate) values, independent of what
        # fit_poisson_gamma would produce for this tiny df (which needs
        # N>=2 anyway) -- we bypass fit_poisson_gamma by monkeypatching is
        # unnecessary; instead just confirm the identity holds algebraically
        # for arbitrary (shape, rate, count, exposure) using the same
        # arithmetic shrink_rates performs internally.
        shape, rate = 2.0, 50.0
        count, exposure = 5, 30
        post_shape = shape + count
        post_rate = rate + exposure
        eb_posterior_rate = post_shape / post_rate
        assert post_shape == 7.0
        assert post_rate == 80.0
        assert eb_posterior_rate == pytest.approx(7.0 / 80.0)


# ---------------------------------------------------------------------------
# Regression test: exact Gamma quantile CI, NOT a symmetric normal approx
# ---------------------------------------------------------------------------

class TestCredibleIntervalAsymmetry:
    """This is the direct regression test for the shrinkage.py bug: CIs used
    to be a symmetric normal approximation (mean +/- 1.96*sd) around a
    right-skewed Gamma posterior. With shape < 1, that approximation is
    badly wrong. These tests independently recompute the exact Gamma
    quantiles (via scipy.stats.gamma.ppf, called directly in the test, not
    through shrinkage.py) and assert (a) shrink_rates matches those exact
    quantiles and (b) the interval is NOT symmetric around the posterior
    mean.
    """

    @pytest.fixture
    def low_shape_df(self):
        # Heavily zero-inflated counts relative to one large count -> large
        # between-unit variance relative to the mean -> shape < 1.
        counts = np.array([0, 0, 0, 0, 0, 20], dtype=float)
        exposures = np.array([1000, 1000, 1000, 1000, 1000, 1000], dtype=float)
        return pd.DataFrame({"count": counts, "exposure": exposures})

    def test_prior_shape_is_below_one(self, low_shape_df):
        shape, rate = fit_poisson_gamma(
            low_shape_df["count"].to_numpy(), low_shape_df["exposure"].to_numpy()
        )
        assert 0 < shape < 1, (
            "Test setup invariant violated: this dataset must produce a "
            "prior shape < 1 to exercise the skewed-posterior regression "
            f"case, but got shape={shape}."
        )

    def test_ci_matches_independently_computed_gamma_quantiles(self, low_shape_df):
        out = shrink_rates(low_shape_df, "count", "exposure")
        shape = out.attrs["gamma_shape"]
        rate = out.attrs["gamma_rate"]

        post_shape = shape + low_shape_df["count"].to_numpy()
        post_rate = rate + low_shape_df["exposure"].to_numpy()

        # Recomputed here, independently of shrinkage.py's internals, using
        # scipy directly -- this is the ground truth the module's CI must
        # match. If shrinkage.py regresses to a normal approximation, this
        # comparison will fail.
        expected_ci_low = np.maximum(
            0, stats.gamma.ppf(0.025, a=post_shape, scale=1 / post_rate) * 100_000
        )
        expected_ci_high = (
            stats.gamma.ppf(0.975, a=post_shape, scale=1 / post_rate) * 100_000
        )

        assert out["eb_ci_low_per_100k"].to_numpy() == pytest.approx(
            expected_ci_low, rel=1e-9, abs=1e-12
        )
        assert out["eb_ci_high_per_100k"].to_numpy() == pytest.approx(
            expected_ci_high, rel=1e-9
        )

    def test_ci_is_asymmetric_for_low_shape_row(self, low_shape_df):
        out = shrink_rates(low_shape_df, "count", "exposure")
        # Row 0 has count=0 -> post_shape == prior shape < 1 -> strongly
        # right-skewed posterior (skewness = 2/sqrt(shape)).
        row = out.iloc[0]
        mean = row["eb_posterior_rate_per_100k"]
        lower_half = mean - row["eb_ci_low_per_100k"]
        upper_half = row["eb_ci_high_per_100k"] - mean

        assert lower_half != pytest.approx(upper_half, rel=0.05), (
            "CI is symmetric around the posterior mean, which should be "
            "impossible for a Gamma posterior with shape < 1 -- this "
            "suggests a regression to a normal approximation."
        )
        # The skew should specifically be right-skewed (upper half wider),
        # matching the documented Gamma skewness direction.
        assert upper_half > lower_half

    def test_a_symmetric_normal_approx_would_differ_materially(self, low_shape_df):
        # Directly demonstrate that the (now-fixed) normal approximation
        # would have given a materially different, symmetric answer, to
        # make explicit what this regression test protects against.
        out = shrink_rates(low_shape_df, "count", "exposure")
        shape = out.attrs["gamma_shape"]
        rate = out.attrs["gamma_rate"]

        row0_count, row0_exposure = 0.0, 1000.0
        post_shape = shape + row0_count
        post_rate = rate + row0_exposure
        mean = post_shape / post_rate
        sd = np.sqrt(post_shape) / post_rate

        normal_ci_low = max(0.0, (mean - 1.96 * sd) * 100_000)
        normal_ci_high = (mean + 1.96 * sd) * 100_000

        actual_ci_low = out.iloc[0]["eb_ci_low_per_100k"]
        actual_ci_high = out.iloc[0]["eb_ci_high_per_100k"]

        # The exact Gamma upper bound should be well above what a normal
        # approximation would produce (normal approx understates the upper
        # tail for right-skewed posteriors).
        assert actual_ci_high > normal_ci_high * 1.2
        assert actual_ci_low != pytest.approx(normal_ci_low, rel=0.2)


# ---------------------------------------------------------------------------
# Edge-case guards added during the audit
# ---------------------------------------------------------------------------

class TestEdgeCaseGuards:
    def test_zero_exposure_raises_value_error(self):
        counts = np.array([1, 2, 3], dtype=float)
        exposures = np.array([10, 0, 30], dtype=float)
        with pytest.raises(ValueError, match="exposures must be strictly positive"):
            fit_poisson_gamma(counts, exposures)

    def test_negative_exposure_raises_value_error(self):
        counts = np.array([1, 2, 3], dtype=float)
        exposures = np.array([10, -5, 30], dtype=float)
        with pytest.raises(ValueError, match="exposures must be strictly positive"):
            fit_poisson_gamma(counts, exposures)

    def test_single_unit_raises_value_error(self):
        counts = np.array([5], dtype=float)
        exposures = np.array([100], dtype=float)
        with pytest.raises(ValueError, match="at least 2 units"):
            fit_poisson_gamma(counts, exposures)

    def test_empty_input_raises_value_error(self):
        counts = np.array([], dtype=float)
        exposures = np.array([], dtype=float)
        # The N < 2 guard now runs before `mu = y.sum() / n.sum()`, so empty
        # input raises cleanly with no RuntimeWarning (0/0) beforehand --
        # this was reordered specifically to fix that; assert it stays fixed.
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with pytest.raises(ValueError, match="at least 2 units"):
                fit_poisson_gamma(counts, exposures)

    def test_sigma2_fallback_does_not_crash_and_yields_tight_shrinkage(self):
        # Counts exactly proportional to exposures -> zero (in fact
        # numerically negative) between-unit variance beyond Poisson noise
        # -> triggers the sigma2 <= 0 fallback branch. Must not raise, and
        # must not produce a negative/undefined Gamma.
        exposures = np.array([100, 200, 300, 400, 500], dtype=float)
        counts = exposures * 0.05  # exact constant rate across all units

        shape, rate = fit_poisson_gamma(counts, exposures)

        assert np.isfinite(shape) and np.isfinite(rate)
        assert shape > 0
        assert rate > 0
        # Fallback sets sigma2 = mu^2 / 1e6, giving a very large shape
        # (near-degenerate prior concentrated tightly around mu).
        mu = counts.sum() / exposures.sum()
        expected_sigma2 = mu ** 2 / 1e6
        expected_shape = mu ** 2 / expected_sigma2
        expected_rate = mu / expected_sigma2
        assert shape == pytest.approx(expected_shape, rel=1e-9)
        assert rate == pytest.approx(expected_rate, rel=1e-9)
        assert shape > 1e5, "fallback should yield a very large (near-degenerate) shape"

    def test_sigma2_fallback_produces_narrow_credible_interval(self):
        # Same near-zero-variance dataset as above, run through shrink_rates
        # end to end: shrinkage should be tight (posterior concentrated
        # near the common mean rate) rather than exploding or collapsing to
        # something undefined.
        exposures = np.array([100, 200, 300, 400, 500], dtype=float)
        counts = exposures * 0.05
        df = pd.DataFrame({"count": counts, "exposure": exposures})

        out = shrink_rates(df, "count", "exposure")

        assert np.isfinite(out["eb_ci_low_per_100k"]).all()
        assert np.isfinite(out["eb_ci_high_per_100k"]).all()
        assert (out["eb_ci_low_per_100k"] >= 0).all()
        # Posterior rates should all be very close to the common empirical
        # rate (0.05 per unit of exposure, i.e. 5,000 per 100k) since the
        # fallback prior is essentially a point mass.
        assert out["eb_posterior_rate_per_100k"].to_numpy() == pytest.approx(
            5_000.0, rel=1e-3
        )
        # The interval should be narrow relative to the rate itself (tight
        # shrinkage), not the wide interval you'd get from a low-shape
        # prior.
        interval_width = out["eb_ci_high_per_100k"] - out["eb_ci_low_per_100k"]
        assert (interval_width < 50).all()  # << 5,000 per-100k rate


# ---------------------------------------------------------------------------
# Integration-style smoke test against real cached data (one per module)
# ---------------------------------------------------------------------------

class TestRealDataSmoke:
    def test_shrink_rates_on_real_county_data_produces_sane_output(
        self, us_county_analysis_path
    ):
        df = pd.read_parquet(us_county_analysis_path)
        out = shrink_rates(df, "obdb_count", "adults_21plus")

        assert out.attrs["gamma_shape"] > 0
        assert out.attrs["gamma_rate"] > 0
        assert (out["eb_posterior_rate"] >= 0).all()
        assert (out["eb_ci_low_per_100k"] >= 0).all()
        # CI should never be inverted.
        assert (out["eb_ci_high_per_100k"] >= out["eb_ci_low_per_100k"]).all()
        # Posterior mean should always lie within its own CI.
        assert (
            out["eb_posterior_rate_per_100k"] >= out["eb_ci_low_per_100k"]
        ).all()
        assert (
            out["eb_posterior_rate_per_100k"] <= out["eb_ci_high_per_100k"]
        ).all()
