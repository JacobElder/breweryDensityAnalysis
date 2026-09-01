"""Regression tests for src/breweries/capture_rate_model.py (OBDB
coverage-correction model: calibrated states + WLS-regression pooled
extrapolation for everyone else).

Two bugs this module previously had, both targeted directly below:

1. The log-linear density extrapolation is unbounded above and could push
   the point estimate and/or CI bounds for very dense counties (e.g.
   Manhattan-like densities) over 1.0 -- nonsensical for a fraction of a
   true population. Fixed by clipping capture_rate/ci_low/ci_high at 1.0.
   See TestDensityClipRegression.

2. apply_correction() used to check `if result["ci_low"]:` (falsy-check)
   instead of `if result["ci_low"] is not None:` when deciding whether to
   add corrected_low/corrected_high keys -- a bug if ci_low could ever
   legitimately be 0.0 (falsy but not None). The current contract is that
   for source == "calibrated", ci_low is always None and those keys must
   never appear. See TestApplyCorrectionKeyContract.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from breweries.capture_rate_model import (
    BETWEEN_STATE_LOG_SD,
    CALIBRATED_STATE_CAPTURE_RATES,
    LOG_DENSITY_COEF,
    POOLED_CAPTURE_RATE,
    _mean_log_density,
    apply_correction,
    correction_factor,
)


# ---------------------------------------------------------------------------
# Calibrated-state contract
# ---------------------------------------------------------------------------

class TestCalibratedState:
    @pytest.mark.parametrize("state", list(CALIBRATED_STATE_CAPTURE_RATES.keys()))
    def test_calibrated_state_returns_exact_empirical_rate(self, state):
        result = correction_factor(state)
        # min(..., 1.0): TX's raw empirical rate is 1.222 (TABC's own license
        # table is documented to undercount, not evidence OBDB over-counts —
        # see the module docstring), and correction_factor() clips every
        # capture rate at 1.0 since it's a fraction of a true population.
        assert result["capture_rate"] == min(CALIBRATED_STATE_CAPTURE_RATES[state], 1.0)
        assert result["source"] == "calibrated"
        assert result["ci_low"] is None
        assert result["ci_high"] is None

    def test_calibrated_state_ignores_log_density(self):
        # A calibrated state's rate must not be perturbed by log_density --
        # it's the direct empirical rate, not model-extrapolated.
        result_no_density = correction_factor("NC")
        result_with_density = correction_factor("NC", log_density=20.0)
        assert result_no_density == result_with_density


# ---------------------------------------------------------------------------
# Uncalibrated state, no log_density -> falls back to pooled rate
# ---------------------------------------------------------------------------

class TestUncalibratedStateNoDensity:
    def test_uncalibrated_state_without_density_returns_pooled_rate(self):
        assert "MT" not in CALIBRATED_STATE_CAPTURE_RATES
        result = correction_factor("MT", log_density=None)
        assert result["capture_rate"] == pytest.approx(POOLED_CAPTURE_RATE)
        assert result["source"] == "pooled_extrapolation"
        assert result["ci_low"] is not None
        assert result["ci_high"] is not None

    def test_pooled_ci_matches_independently_computed_bounds(self):
        # Recompute the CI bounds independently (not by re-deriving from
        # correction_factor's own internals) using the module's own
        # documented constants, to guard against a future change to the
        # interval formula going unnoticed.
        result = correction_factor("MT", log_density=None)
        log_rate = np.log(POOLED_CAPTURE_RATE)
        expected_ci_low = min(np.exp(log_rate - 1.96 * BETWEEN_STATE_LOG_SD), 1.0)
        expected_ci_high = min(np.exp(log_rate + 1.96 * BETWEEN_STATE_LOG_SD), 1.0)
        assert result["ci_low"] == pytest.approx(expected_ci_low)
        assert result["ci_high"] == pytest.approx(expected_ci_high)


# ---------------------------------------------------------------------------
# Density adjustment: monotonicity
# ---------------------------------------------------------------------------

class TestDensityMonotonicity:
    def test_higher_log_density_yields_higher_capture_rate(self):
        mean_log_density = _mean_log_density()
        low = correction_factor("MT", log_density=mean_log_density)
        high = correction_factor("MT", log_density=mean_log_density + 2.0)
        assert high["capture_rate"] > low["capture_rate"]

    def test_lower_log_density_yields_lower_capture_rate(self):
        mean_log_density = _mean_log_density()
        baseline = correction_factor("MT", log_density=mean_log_density)
        lower = correction_factor("MT", log_density=mean_log_density - 2.0)
        assert lower["capture_rate"] < baseline["capture_rate"]

    def test_density_adjustment_matches_documented_coefficient(self):
        # At mean_log_density the adjustment should be exactly zero (i.e.
        # capture_rate == POOLED_CAPTURE_RATE); moving by +1 log-density
        # unit should scale the rate by exp(LOG_DENSITY_COEF), as
        # documented in the module ("WLS slope, per unit increase in
        # log(people per sq mi)").
        mean_log_density = _mean_log_density()
        baseline = correction_factor("MT", log_density=mean_log_density)
        assert baseline["capture_rate"] == pytest.approx(POOLED_CAPTURE_RATE)

        bumped = correction_factor("MT", log_density=mean_log_density + 1.0)
        expected = POOLED_CAPTURE_RATE * np.exp(LOG_DENSITY_COEF)
        assert bumped["capture_rate"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Regression test: capture_rate / CI must never exceed 1.0
# ---------------------------------------------------------------------------

class TestDensityClipRegression:
    def _log_density_that_would_exceed_one_unclipped(self) -> float:
        # Derive a log_density high enough that the raw (unclipped)
        # log-linear extrapolation exceeds 1.0, given the documented
        # POOLED_CAPTURE_RATE, LOG_DENSITY_COEF, and mean log-density.
        # rate > 1  <=>  log(POOLED_CAPTURE_RATE) + COEF*(x - mean) > 0
        #            <=>  x > mean - log(POOLED_CAPTURE_RATE)/COEF
        mean_log_density = _mean_log_density()
        threshold = mean_log_density - np.log(POOLED_CAPTURE_RATE) / LOG_DENSITY_COEF
        return threshold + 2.0  # comfortably past the threshold

    def test_extreme_density_would_exceed_one_unclipped(self):
        # Sanity-check the derivation above: confirm the *raw* exponential
        # really would cross 1.0 for this log_density, so the test below is
        # actually exercising the clip rather than passing vacuously.
        x = self._log_density_that_would_exceed_one_unclipped()
        mean_log_density = _mean_log_density()
        raw_log_rate = np.log(POOLED_CAPTURE_RATE) + LOG_DENSITY_COEF * (
            x - mean_log_density
        )
        assert np.exp(raw_log_rate) > 1.0

    def test_capture_rate_clipped_at_one_for_extreme_density(self):
        x = self._log_density_that_would_exceed_one_unclipped()
        result = correction_factor("MT", log_density=x)
        assert result["capture_rate"] <= 1.0
        assert result["capture_rate"] == pytest.approx(1.0)
        assert result["ci_high"] <= 1.0
        assert result["ci_low"] >= 0.0

    def test_manhattan_like_density_from_real_data_is_clipped(self):
        # Manhattan-scale density (~72,000 people/sq mi, i.e. the densest real
        # US counties) should not produce a >1.0 "capture rate" for a state
        # going through the pooled-extrapolation path. Deliberately NOT "NY"
        # here: New York is itself a calibrated state (and Manhattan is the
        # real-world example of this density), so passing "NY" would exercise
        # the calibrated branch (ci_high=None) instead of the clip logic this
        # test targets -- use an uncalibrated state so the density-driven
        # extrapolation path is what's actually under test. (NJ was this
        # test's example state originally, but a later round of calibration
        # added NJ as a directly-measured state -- AL remains uncalibrated,
        # confirmed no bulk open-data source.)
        assert "AL" not in CALIBRATED_STATE_CAPTURE_RATES
        manhattan_log_density = np.log(72_000)
        result = correction_factor("AL", log_density=manhattan_log_density)
        assert result["capture_rate"] <= 1.0
        assert result["capture_rate"] >= 0.0
        assert result["ci_high"] <= 1.0
        assert result["ci_low"] >= 0.0

    def test_capture_rate_never_negative_across_a_wide_density_range(self):
        for x in np.linspace(-5, 20, 25):
            result = correction_factor("MT", log_density=float(x))
            assert 0.0 <= result["capture_rate"] <= 1.0
            assert 0.0 <= result["ci_low"] <= 1.0
            assert 0.0 <= result["ci_high"] <= 1.0


# ---------------------------------------------------------------------------
# Regression test: apply_correction key contract (the `is not None` fix)
# ---------------------------------------------------------------------------

class TestApplyCorrectionKeyContract:
    def test_calibrated_source_never_adds_corrected_low_high_keys(self):
        # For a calibrated state, ci_low/ci_high are always None, so
        # apply_correction must never add corrected_low/corrected_high.
        # This is the regression test for the `if result["ci_low"]:` vs
        # `if result["ci_low"] is not None:` bug -- both formulations agree
        # when ci_low is None (falsy either way), but this test pins the
        # actual current contract so any change that starts adding these
        # keys for calibrated states is caught.
        for state in CALIBRATED_STATE_CAPTURE_RATES:
            out = apply_correction(obdb_count=100, state=state)
            assert "corrected_low" not in out
            assert "corrected_high" not in out
            assert out["ci_low"] is None
            assert out["ci_high"] is None

    def test_pooled_extrapolation_always_adds_corrected_low_high_keys(self):
        out = apply_correction(obdb_count=100, state="MT", log_density=None)
        assert out["ci_low"] is not None
        assert "corrected_low" in out
        assert "corrected_high" in out


# ---------------------------------------------------------------------------
# apply_correction arithmetic
# ---------------------------------------------------------------------------

class TestApplyCorrectionMath:
    def test_corrected_estimate_equals_count_divided_by_capture_rate(self):
        out = apply_correction(obdb_count=50, state="NC")
        assert out["corrected_estimate"] == pytest.approx(
            50 / CALIBRATED_STATE_CAPTURE_RATES["NC"]
        )

    def test_corrected_estimate_for_pooled_extrapolation(self):
        out = apply_correction(obdb_count=200, state="MT", log_density=None)
        assert out["corrected_estimate"] == pytest.approx(
            200 / POOLED_CAPTURE_RATE
        )

    def test_corrected_low_high_are_inverse_of_ci_bounds(self):
        # corrected_low should use the *high* end of the capture-rate CI
        # (higher assumed capture rate -> smaller corrected estimate), and
        # vice versa -- this inversion direction is easy to get backwards.
        out = apply_correction(obdb_count=100, state="MT", log_density=None)
        assert out["corrected_low"] == pytest.approx(100 / out["ci_high"])
        assert out["corrected_high"] == pytest.approx(100 / out["ci_low"])
        assert out["corrected_low"] <= out["corrected_estimate"] <= out["corrected_high"]

    def test_obdb_count_passed_through_unchanged(self):
        out = apply_correction(obdb_count=77, state="MI")
        assert out["obdb_count"] == 77


# ---------------------------------------------------------------------------
# Integration-style smoke test against real cached data
# ---------------------------------------------------------------------------

class TestRealDataSmoke:
    def test_correction_factor_reproduces_stored_capture_rates(
        self, us_county_analysis_path
    ):
        df = pd.read_parquet(us_county_analysis_path)
        # Sample a handful of rows across both correction sources and
        # confirm correction_factor() reproduces the stored capture_rate
        # exactly (this is what the real build pipeline calls under the
        # hood), including for the real densest county in the dataset
        # (which should be clipped at 1.0 -- the real-world analogue of the
        # synthetic clip regression test above).
        calibrated_rows = df[df["correction_source"] == "calibrated"].head(3)
        for _, row in calibrated_rows.iterrows():
            result = correction_factor(row["state_abbr"])
            assert result["capture_rate"] == pytest.approx(row["capture_rate"])

        pooled_rows = df[df["correction_source"] == "pooled_extrapolation"].head(3)
        for _, row in pooled_rows.iterrows():
            result = correction_factor(
                row["state_abbr"], log_density=np.log(row["density_per_sqmi"])
            )
            assert result["capture_rate"] == pytest.approx(row["capture_rate"])

        densest_row = df.loc[df["density_per_sqmi"].idxmax()]
        result = correction_factor(
            densest_row["state_abbr"], log_density=np.log(densest_row["density_per_sqmi"])
        )
        assert result["capture_rate"] <= 1.0
        assert result["capture_rate"] == pytest.approx(densest_row["capture_rate"])
