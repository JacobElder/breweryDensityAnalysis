"""State-level rollup of the county-level brewery density analysis.

Every ranking/output artifact in this project so far is county-level, even
though state is a first-class unit in the project's own methodology: the
capture-rate correction model (src/breweries/capture_rate_model.py) is
calibrated PER STATE -- 13 states have a directly-measured empirical capture
rate from real licensee registries, and every other state falls back to a
pooled WLS-regression estimate (see that module's docstring for the full
rationale). This script is the state-level summary table that calibration
work implies but nothing currently produces.

One row per state (+ DC). For each state:

- whether it's a directly-calibrated state or a pooled-estimate state, and
  the capture rate itself. Calibrated states have one fixed capture_rate
  (src/breweries/capture_rate_model.py::CALIBRATED_STATE_CAPTURE_RATES,
  clipped at 1.0). Pooled states have a capture_rate that varies county to
  county (density-adjusted), so this table reports the adults-21+-weighted
  mean across the state's counties -- a single representative number, not a
  new estimate.
- total obdb_count and obdb_corrected, summed across counties, and the
  resulting "gap" (corrected - obdb): how many additional breweries the
  correction implies exist beyond what OBDB captured.
- population-weighted mean corrected rate per 100k adults 21+, weighting
  each county's corrected_rate_per_100k_21plus by its adults_21plus. A naive
  county average would let a handful of tiny counties (some with 0
  population-adjacent breweries and huge relative swings) dominate a state's
  number as much as its largest metro -- weighting by population is the
  only way this reflects "how dense is this state, experienced by the
  people who live there."
- among the state's population>=50k counties that appear in
  us_county_raw_vs_corrected_rankings.csv, the mean and median rank_change
  (positive = moved UP in the national ranking once corrected) -- a
  state-level view of how much the correction reshuffles that state's
  counties.
- the state's own rank by population-weighted corrected rate (1 = highest
  density state).

Data sources (all pre-existing, read-only):
- data/processed/us_county_analysis.parquet -- county-level base table.
- data/processed/us_county_raw_vs_corrected_rankings.csv -- population>=50k
  counties only, with raw_rank/corrected_rank/rank_change.
- src/breweries/capture_rate_model.py -- CALIBRATED_STATE_CAPTURE_RATES, the
  authoritative list of which states are directly calibrated.
- src/breweries/state_fips.py -- STATE_NAME_TO_ABBR, inverted here for
  state_abbr -> state_name.

Output: data/processed/state_rollup_table.csv, sorted by
pop_weighted_corrected_rate_per_100k descending.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from breweries.capture_rate_model import CALIBRATED_STATE_CAPTURE_RATES
from breweries.state_fips import STATE_NAME_TO_ABBR

COUNTY_ANALYSIS_PATH = "data/processed/us_county_analysis.parquet"
RANK_CHANGE_PATH = "data/processed/us_county_raw_vs_corrected_rankings.csv"
OUT_PATH = "data/processed/state_rollup_table.csv"

ABBR_TO_STATE_NAME = {abbr: name for name, abbr in STATE_NAME_TO_ABBR.items()}

pd.set_option("display.width", 200)
pd.set_option("display.max_rows", 60)


def _weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    weights = weights.fillna(0.0)
    total_weight = weights.sum()
    if total_weight <= 0:
        return float("nan")
    return float((values * weights).sum() / total_weight)


def build_state_rollup(county_df: pd.DataFrame, rank_change_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for state_abbr, sub in county_df.groupby("state_abbr"):
        is_calibrated = state_abbr in CALIBRATED_STATE_CAPTURE_RATES
        capture_rate = _weighted_mean(sub["capture_rate"], sub["adults_21plus"])

        obdb_total = int(sub["obdb_count"].sum())
        corrected_total = float(sub["obdb_corrected"].sum())

        pop_weighted_rate = _weighted_mean(sub["corrected_rate_per_100k_21plus"], sub["adults_21plus"])

        state_rank_changes = rank_change_df.loc[rank_change_df["state_abbr"] == state_abbr, "rank_change"]

        rows.append({
            "state_abbr": state_abbr,
            "state_name": ABBR_TO_STATE_NAME.get(state_abbr, np.nan),
            "correction_source": "calibrated" if is_calibrated else "pooled_extrapolation",
            "capture_rate": capture_rate,
            "obdb_count_total": obdb_total,
            "obdb_corrected_total": corrected_total,
            "corrected_minus_raw_gap": corrected_total - obdb_total,
            "pop_weighted_corrected_rate_per_100k": pop_weighted_rate,
            "n_counties": int(len(sub)),
            "n_ranked_counties_pop50k": int(len(state_rank_changes)),
            "mean_rank_change_pop50k": float(state_rank_changes.mean()) if len(state_rank_changes) else np.nan,
            "median_rank_change_pop50k": float(state_rank_changes.median()) if len(state_rank_changes) else np.nan,
        })

    out = pd.DataFrame(rows)
    out = out.sort_values("pop_weighted_corrected_rate_per_100k", ascending=False).reset_index(drop=True)
    out["state_density_rank"] = out["pop_weighted_corrected_rate_per_100k"].rank(
        ascending=False, method="min"
    ).astype(int)
    return out


def main() -> None:
    county_df = pd.read_parquet(COUNTY_ANALYSIS_PATH)
    rank_change_df = pd.read_csv(RANK_CHANGE_PATH)

    print("=" * 78)
    print("State-level rollup of the capture-rate-corrected brewery density analysis")
    print("=" * 78)

    state_table = build_state_rollup(county_df, rank_change_df)
    state_table.to_csv(OUT_PATH, index=False)
    print(f"\nWrote {OUT_PATH} ({len(state_table)} states/DC)")

    display_cols = [
        "state_density_rank", "state_abbr", "state_name", "correction_source", "capture_rate",
        "obdb_count_total", "obdb_corrected_total", "corrected_minus_raw_gap",
        "pop_weighted_corrected_rate_per_100k", "n_ranked_counties_pop50k",
        "mean_rank_change_pop50k", "median_rank_change_pop50k",
    ]
    print("\nFull table, sorted by population-weighted corrected rate per 100k adults 21+:")
    print(state_table[display_cols].to_string(index=False, float_format=lambda v: f"{v:,.3f}"))

    print("\n" + "=" * 78)
    print("Top 10 states by population-weighted corrected rate per 100k adults 21+")
    print("=" * 78)
    print(state_table[display_cols].head(10).to_string(index=False, float_format=lambda v: f"{v:,.3f}"))

    print("\n" + "=" * 78)
    print("Biggest raw-vs-corrected count gaps (corrected_total - obdb_count_total)")
    print("=" * 78)
    biggest_gap = state_table.sort_values("corrected_minus_raw_gap", ascending=False).head(15)
    print(biggest_gap[["state_abbr", "state_name", "correction_source", "capture_rate",
                        "obdb_count_total", "obdb_corrected_total", "corrected_minus_raw_gap"]]
          .to_string(index=False, float_format=lambda v: f"{v:,.2f}"))

    print("\n" + "=" * 78)
    print("Calibrated vs. pooled-estimate: mean_rank_change_pop50k comparison")
    print("=" * 78)
    calibrated = state_table[state_table["correction_source"] == "calibrated"]
    pooled = state_table[state_table["correction_source"] == "pooled_extrapolation"]
    for label, sub in [("Calibrated (13 states)", calibrated), ("Pooled/regression-estimated", pooled)]:
        vals = sub["mean_rank_change_pop50k"].dropna()
        print(f"{label}: n={len(vals)}, "
              f"mean of state means={vals.mean():.2f}, "
              f"median of state means={vals.median():.2f}, "
              f"sd of state means={vals.std():.2f}, "
              f"range=[{vals.min():.2f}, {vals.max():.2f}]")

    print("\nCalibrated states, individually (mean_rank_change_pop50k):")
    print(calibrated[["state_abbr", "capture_rate", "mean_rank_change_pop50k", "median_rank_change_pop50k",
                       "n_ranked_counties_pop50k"]]
          .sort_values("mean_rank_change_pop50k", ascending=False)
          .to_string(index=False, float_format=lambda v: f"{v:,.2f}"))

    print("\nPooled-estimate states with the largest |mean_rank_change_pop50k| (top 15 by magnitude):")
    pooled_sorted = pooled.copy()
    pooled_sorted["_abs"] = pooled_sorted["mean_rank_change_pop50k"].abs()
    print(pooled_sorted.sort_values("_abs", ascending=False).head(15)
          [["state_abbr", "capture_rate", "mean_rank_change_pop50k", "median_rank_change_pop50k",
            "n_ranked_counties_pop50k"]]
          .to_string(index=False, float_format=lambda v: f"{v:,.2f}"))


if __name__ == "__main__":
    main()
