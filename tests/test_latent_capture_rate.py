"""Tests for src/breweries/latent_capture_rate.py.

The module's whole purpose is that uncertainty about the capture rate must
show up as uncertainty in the corrected rate. The central test is therefore
not "does it divide correctly" but "does the interval WIDEN" -- dividing by a
point estimate also produces a corrected number, and that is precisely the
mistake being avoided.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from breweries import latent_capture_rate as lcr
from breweries.capture_rate_model import CALIBRATED_STATE_CAPTURE_RATES


class TestStateCapturePriors:
    def test_calibrated_state_uses_measured_rate_and_tight_sd(self):
        p = lcr.state_capture_priors(["GA"])
        assert p["source"].iloc[0] == "calibrated"
        assert p["sd_log_c"].iloc[0] == lcr.CALIBRATED_LOG_SD
        expected = min(CALIBRATED_STATE_CAPTURE_RATES["GA"], 1.0)
        assert np.isclose(np.exp(p["mu_log_c"].iloc[0]), expected)

    def test_uncalibrated_state_gets_much_wider_sd(self):
        """The distinction the whole module rests on: an extrapolated capture
        rate must not be treated like a measured one."""
        p = lcr.state_capture_priors(["GA", "OH"])
        cal = p[p.state_abbr == "GA"]["sd_log_c"].iloc[0]
        pooled = p[p.state_abbr == "OH"]["sd_log_c"].iloc[0]
        assert p[p.state_abbr == "OH"]["source"].iloc[0] == "pooled_extrapolation"
        assert pooled > 3 * cal

    def test_every_state_gets_a_prior(self):
        states = ["GA", "OR", "OH", "MN", "TX", "WY"]
        p = lcr.state_capture_priors(states)
        assert list(p["state_abbr"]) == states
        assert p["sd_log_c"].gt(0).all()
        assert np.isfinite(p["mu_log_c"]).all()


class TestSampleLogCapture:
    def test_capture_rate_never_exceeds_one(self):
        """A capture rate is a share of a true population. States whose raw
        ratio was clipped to 1.0 (WY 1.286, MO 1.662, TX 1.222) sit right at
        the boundary, so truncation is load-bearing, not decorative."""
        p = lcr.state_capture_priors(["TX", "WV", "WY"])
        draws, _ = lcr.sample_log_capture(p, 5000, seed=0)
        assert (draws <= lcr.LOG_CAPTURE_UPPER + 1e-12).all()
        assert (np.exp(draws) <= 1.0 + 1e-12).all()

    def test_draws_are_centred_near_the_prior_mean_when_far_from_the_bound(self):
        p = lcr.state_capture_priors(["GA"])  # ~0.476, far below 1.0
        draws, _ = lcr.sample_log_capture(p, 20000, seed=0)
        assert np.isclose(draws.mean(), p["mu_log_c"].iloc[0], atol=0.01)

    def test_truncation_pulls_a_boundary_state_below_one(self):
        p = lcr.state_capture_priors(["TX"])  # clipped to exactly 1.0
        draws, _ = lcr.sample_log_capture(p, 20000, seed=0)
        assert np.exp(draws).mean() < 1.0

    def test_shape_and_determinism(self):
        p = lcr.state_capture_priors(["GA", "OH"])
        a, states = lcr.sample_log_capture(p, 100, seed=7)
        b, _ = lcr.sample_log_capture(p, 100, seed=7)
        assert a.shape == (100, 2)
        assert states == ["GA", "OH"]
        np.testing.assert_allclose(a, b)


class TestTrueRateSamples:
    @staticmethod
    def _observed(n_draws=20000, n_counties=2, rate=5.0, sd=0.05):
        rng = np.random.default_rng(0)
        return rate * np.exp(rng.normal(0, sd, size=(n_draws, n_counties)))

    def test_true_rate_exceeds_observed_rate(self):
        """Capture rate < 1 means more breweries exist than are listed."""
        p = lcr.state_capture_priors(["GA"])
        obs = self._observed(n_counties=1)
        true = lcr.true_rate_samples(obs, np.array([0]), p)
        assert np.median(true) > np.median(obs)

    def test_interval_widens_relative_to_a_point_correction(self):
        """THE test. Dividing by the point estimate shifts the interval;
        convolving the prior must also widen it."""
        p = lcr.state_capture_priors(["OH"])  # pooled -> wide prior
        obs = self._observed(n_counties=1)
        point = np.exp(p["mu_log_c"].iloc[0])

        w = lambda s: np.log(s["ci_high"].iloc[0] / s["ci_low"].iloc[0])
        fixed_w = w(lcr.summarize(obs / point))
        latent_w = w(lcr.summarize(lcr.true_rate_samples(obs, np.array([0]), p)))

        assert np.isclose(fixed_w, w(lcr.summarize(obs)), atol=1e-9), \
            "dividing by a constant must not change interval width at all"
        assert latent_w > fixed_w * 2, "latent treatment must widen the interval substantially"

    def test_pooled_state_widens_more_than_calibrated_state(self):
        """Uncertainty must land where the correction is least trustworthy --
        the failure mode being avoided is the worst-covered states coming out
        looking the most confident."""
        obs = self._observed(n_counties=1)
        w = lambda st: np.log(
            (s := lcr.summarize(lcr.true_rate_samples(
                obs, np.array([0]), lcr.state_capture_priors([st]))))["ci_high"].iloc[0]
            / s["ci_low"].iloc[0])
        assert w("OH") > w("GA")

    def test_per_county_state_mapping_is_respected(self):
        p = lcr.state_capture_priors(["GA", "OR"])  # 0.476 vs 0.903
        obs = self._observed(n_counties=2, sd=1e-6)
        true = lcr.true_rate_samples(obs, np.array([0, 1]), p)
        # GA's lower capture rate implies a larger upward correction.
        assert np.median(true[:, 0]) > np.median(true[:, 1])


class TestSummarize:
    def test_orders_and_brackets_the_median(self):
        rng = np.random.default_rng(0)
        s = lcr.summarize(rng.lognormal(0, 0.5, size=(10000, 3)))
        assert (s["ci_low"] < s["median"]).all()
        assert (s["median"] < s["ci_high"]).all()

    def test_uses_median_not_mean(self):
        """Matches the convention used for the observed-scale rates: exp() of
        a wide-variance quantity has mean >> median."""
        rng = np.random.default_rng(0)
        x = rng.lognormal(0, 1.0, size=(50000, 1))
        s = lcr.summarize(x)
        assert abs(s["median"].iloc[0] - 1.0) < 0.05
        assert x.mean() > 1.4
