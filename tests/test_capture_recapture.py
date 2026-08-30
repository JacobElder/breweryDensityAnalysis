"""Regression tests for src/breweries/capture_recapture.py (record matching
+ Lincoln-Petersen/Chapman capture-recapture estimator).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from breweries.capture_recapture import lincoln_petersen, match_records, normalize_name


# ---------------------------------------------------------------------------
# lincoln_petersen: Chapman estimator, hand-computed
# ---------------------------------------------------------------------------

class TestLincolnPetersen:
    def test_chapman_estimator_matches_hand_computation(self):
        # n1=100, n2=80, m=20 -- simple round numbers, independently
        # computed here via the Chapman formula (not by calling
        # lincoln_petersen a second time):
        #   n_hat = (n1+1)(n2+1)/(m+1) - 1
        #   var   = (n1+1)(n2+1)(n1-m)(n2-m) / [(m+1)^2 (m+2)]
        n1, n2, m = 100, 80, 20
        expected_n_hat = ((n1 + 1) * (n2 + 1)) / (m + 1) - 1
        expected_var = ((n1 + 1) * (n2 + 1) * (n1 - m) * (n2 - m)) / (
            (m + 1) ** 2 * (m + 2)
        )
        expected_se = np.sqrt(expected_var)
        expected_ci_low = expected_n_hat - 1.96 * expected_se
        expected_ci_high = expected_n_hat + 1.96 * expected_se

        # Sanity-check the hand computation against known round values
        # before comparing to the implementation, so a bug in the test's
        # own arithmetic doesn't silently mask a bug in the module (or vice
        # versa).
        assert expected_n_hat == pytest.approx(388.57142857142856, rel=1e-9)
        assert expected_se == pytest.approx(63.619928967117495, rel=1e-9)

        result = lincoln_petersen(n1, n2, m)
        assert result["n1"] == n1
        assert result["n2"] == n2
        assert result["m"] == m
        assert result["n_hat"] == pytest.approx(expected_n_hat, rel=1e-9)
        assert result["se"] == pytest.approx(expected_se, rel=1e-9)
        assert result["ci_low"] == pytest.approx(expected_ci_low, rel=1e-9)
        assert result["ci_high"] == pytest.approx(expected_ci_high, rel=1e-9)

    def test_capture_rates_match_hand_computation(self):
        n1, n2, m = 100, 80, 20
        result = lincoln_petersen(n1, n2, m)
        expected_n_hat = ((n1 + 1) * (n2 + 1)) / (m + 1) - 1
        assert result["capture_rate_1"] == pytest.approx(n1 / expected_n_hat, rel=1e-9)
        assert result["capture_rate_2"] == pytest.approx(n2 / expected_n_hat, rel=1e-9)

    def test_second_hand_computed_case_with_different_numbers(self):
        # A second independent case (n1=50, n2=50, m=10) to make sure the
        # first case wasn't a coincidental match.
        n1, n2, m = 50, 50, 10
        expected_n_hat = ((n1 + 1) * (n2 + 1)) / (m + 1) - 1  # = 2601/11 - 1
        expected_var = ((n1 + 1) * (n2 + 1) * (n1 - m) * (n2 - m)) / (
            (m + 1) ** 2 * (m + 2)
        )
        expected_se = np.sqrt(expected_var)

        result = lincoln_petersen(n1, n2, m)
        assert result["n_hat"] == pytest.approx(expected_n_hat, rel=1e-9)
        assert result["se"] == pytest.approx(expected_se, rel=1e-9)
        assert result["ci_low"] == pytest.approx(
            expected_n_hat - 1.96 * expected_se, rel=1e-9
        )
        assert result["ci_high"] == pytest.approx(
            expected_n_hat + 1.96 * expected_se, rel=1e-9
        )

    def test_n_hat_is_at_least_max_n1_n2(self):
        # Sanity invariant: the estimated true population can never be
        # smaller than either observed list (for any m <= min(n1, n2)).
        result = lincoln_petersen(100, 80, 20)
        assert result["n_hat"] >= max(100, 80)

    def test_perfect_overlap_gives_n_hat_close_to_list_size(self):
        # If m == n1 == n2 (every record in both lists), the true
        # population estimate should be very close to n1/n2 (not exactly
        # equal, since Chapman's +1/-1 correction still applies).
        result = lincoln_petersen(100, 100, 100)
        assert result["n_hat"] == pytest.approx(100, abs=1.5)


# ---------------------------------------------------------------------------
# normalize_name
# ---------------------------------------------------------------------------

class TestNormalizeName:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Asheville Brewing Company", "asheville"),
            ("Wicked Weed Brewing", "wicked weed"),
            ("Catawba Valley Brewing Co.", "catawba valley"),
            ("Old Mecklenburg Brewery", "old mecklenburg"),
            ("Trophy Brewing & Pizza, LLC", "trophy pizza"),
            ("Foothills Brewing Ales", "foothills"),
            ("", ""),
            ("   ", ""),
        ],
    )
    def test_strips_suffix_words_and_punctuation(self, raw, expected):
        assert normalize_name(raw) == expected

    def test_non_string_input_returns_empty_string(self):
        assert normalize_name(None) == ""
        assert normalize_name(123) == ""
        assert normalize_name(float("nan")) == ""

    def test_case_insensitive(self):
        assert normalize_name("TROPHY BREWING COMPANY") == normalize_name(
            "trophy brewing company"
        )

    def test_collapses_internal_whitespace(self):
        assert normalize_name("Deep   River    Brewing") == "deep river"


# ---------------------------------------------------------------------------
# match_records
# ---------------------------------------------------------------------------

class TestMatchRecords:
    @pytest.fixture
    def synthetic_frames(self):
        # Base point roughly in Raleigh, NC. Distances verified directly
        # via the module's own _haversine_m before writing this fixture:
        #   a0 (Trophy)     vs b0 (Trophy)        ~56m,  name score 100 -> MATCH
        #   a1 (Fullsteam)  vs nearest eligible b  name score too low   -> NO MATCH
        #   a2 (Big Boss)   vs b2 (Neuse River)   ~33m,  name score low -> NO MATCH
        #   a3 (Deep River) vs b3 (Deep River Brew) ~44m, name score 80 -> MATCH
        #   a4 (Isolated)   far from every b row                        -> NO MATCH
        #   a5 has NaN coordinates                                      -> NO MATCH
        df_a = pd.DataFrame(
            {
                "name": [
                    "Trophy Brewing Company",
                    "Fullsteam Brewery",
                    "Big Boss Brewing Co",
                    "Deep River Brewing Company",
                    "Isolated Brewing",
                    "NaN Coord Brewing",
                ],
                "lat": [35.7796, 35.7810, 35.7830, 35.7850, 35.9000, np.nan],
                "lon": [-78.6382, -78.6390, -78.6400, -78.6410, -78.9000, -78.0],
            }
        )
        df_b = pd.DataFrame(
            {
                "name": [
                    "Trophy Brewing Co",
                    "Fullsteam Brewery",
                    "Neuse River Brewing Co",
                    "Deep River Brew Co",
                ],
                "lat": [
                    35.7796 + 0.0005,
                    35.7810 + 0.005,
                    35.7830 + 0.0003,
                    35.7850 + 0.0004,
                ],
                "lon": [-78.6382, -78.6390, -78.6400, -78.6410],
            }
        )
        return df_a, df_b

    def test_expected_matches_and_non_matches(self, synthetic_frames):
        df_a, df_b = synthetic_frames
        out = match_records(
            df_a, df_b, "name", "name", "lat", "lon", "lat", "lon"
        )
        assert out["matched_b_index"].tolist() == [0, -1, -1, 3, -1, -1]

    def test_matched_rows_have_name_score_and_distance_populated(self, synthetic_frames):
        df_a, df_b = synthetic_frames
        out = match_records(
            df_a, df_b, "name", "name", "lat", "lon", "lat", "lon"
        )
        assert out.loc[0, "match_name_score"] == pytest.approx(100.0)
        assert out.loc[0, "match_distance_m"] < 300
        assert out.loc[3, "match_name_score"] >= 65
        assert out.loc[3, "match_distance_m"] < 300

    def test_unmatched_rows_have_nan_score_and_distance(self, synthetic_frames):
        df_a, df_b = synthetic_frames
        out = match_records(
            df_a, df_b, "name", "name", "lat", "lon", "lat", "lon"
        )
        for i in [1, 2, 4, 5]:
            assert pd.isna(out.loc[i, "match_name_score"])
            assert pd.isna(out.loc[i, "match_distance_m"])

    def test_each_b_record_used_at_most_once(self, synthetic_frames):
        df_a, df_b = synthetic_frames
        out = match_records(
            df_a, df_b, "name", "name", "lat", "lon", "lat", "lon"
        )
        matched_indices = out.loc[out["matched_b_index"] >= 0, "matched_b_index"]
        assert matched_indices.is_unique

    def test_max_distance_threshold_is_respected(self, synthetic_frames):
        # Tightening max_distance_m below a4's would-be nearest candidate
        # distance shouldn't matter for a4 (already too far), but should
        # newly exclude a match that used to be within range. Shrinking
        # max_distance_m to 40m should drop a3's match (~44m away).
        df_a, df_b = synthetic_frames
        out = match_records(
            df_a, df_b, "name", "name", "lat", "lon", "lat", "lon",
            max_distance_m=40,
        )
        assert out.loc[0, "matched_b_index"] == -1  # was ~56m, now excluded
        assert out.loc[3, "matched_b_index"] == -1  # was ~44m, now excluded

    def test_name_threshold_is_respected(self, synthetic_frames):
        # Raising name_threshold above a3's match score (80) should drop
        # that match while leaving a0's perfect-score match intact.
        df_a, df_b = synthetic_frames
        out = match_records(
            df_a, df_b, "name", "name", "lat", "lon", "lat", "lon",
            name_threshold=90,
        )
        assert out.loc[0, "matched_b_index"] == 0
        assert out.loc[3, "matched_b_index"] == -1

    def test_empty_frames_do_not_crash(self):
        empty_a = pd.DataFrame({"name": [], "lat": [], "lon": []})
        empty_b = pd.DataFrame({"name": [], "lat": [], "lon": []})
        out = match_records(
            empty_a, empty_b, "name", "name", "lat", "lon", "lat", "lon"
        )
        assert len(out) == 0


# ---------------------------------------------------------------------------
# Integration-style smoke test against a real cached match_records output
# ---------------------------------------------------------------------------

class TestRealDataSmoke:
    def test_real_nc_match_output_satisfies_match_records_invariants(
        self, nc_obdb_osm_match_path
    ):
        """data/processed/nc_obdb_osm_match.csv is the real, previously
        generated output of match_records(df_obdb, df_osm, ...) for North
        Carolina (see scripts/nc_capture_recapture.py). The raw OBDB/OSM
        input frames require live source loaders to reconstruct, so rather
        than re-run match_records here, this test checks that the real
        output still satisfies the structural invariants match_records is
        supposed to guarantee: each df_b row used at most once, matched
        rows within the (default) 300m / 65-score thresholds used to
        produce this file, and unmatched rows carrying NaN score/distance.
        """
        df = pd.read_csv(nc_obdb_osm_match_path)

        matched = df[df["matched_b_index"] >= 0]
        unmatched = df[df["matched_b_index"] < 0]

        assert len(matched) + len(unmatched) == len(df)
        assert not matched["matched_b_index"].duplicated().any()
        assert (matched["match_name_score"] >= 65).all()
        assert (matched["match_distance_m"] <= 300).all()
        assert unmatched["match_name_score"].isna().all()
        assert unmatched["match_distance_m"].isna().all()
