"""Fit the OBDB coverage-correction model on the four calibration states.

Pools NC/MI/CO/OR county-level (obdb_count, licensee_count, population density)
data, fits a fixed-effects and a random-intercept (state) log-capture-ratio model,
and writes the pooled dataset used by src/breweries/capture_rate_model.py.

Run this after all four build_{state}_county_dataset.py scripts have produced
their outputs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from breweries.sources import tiger

STATE_LICENSEE_COL = {
    "NC": "abc_permit_count",
    "MI": "lara_permit_count",
    "CO": "liquor_count",
    "OR": "olcc_primary_count",
}
STATE_FIPS = {"NC": "37", "MI": "26", "CO": "08", "OR": "41"}


def load_pooled_counties() -> pd.DataFrame:
    frames = []
    for state, col in STATE_LICENSEE_COL.items():
        df = pd.read_parquet(f"data/processed/{state.lower()}_county_analysis.parquet")
        df = df.rename(columns={col: "licensee_count"})
        df["state"] = state
        frames.append(df[["county_name", "state", "obdb_count", "licensee_count",
                           "adults_21plus", "total_population"]])
    return pd.concat(frames, ignore_index=True)


def load_land_area() -> pd.DataFrame:
    counties = tiger.load_counties()[["STATEFP", "NAME", "ALAND"]]
    fips_to_state = {v: k for k, v in STATE_FIPS.items()}
    counties = counties[counties["STATEFP"].isin(fips_to_state)].copy()
    counties["state"] = counties["STATEFP"].map(fips_to_state)
    counties["county_name"] = counties["NAME"]
    counties["sqmi"] = counties["ALAND"] / 2_589_988
    return counties[["state", "county_name", "sqmi"]]


def main() -> None:
    pooled = load_pooled_counties()
    land = load_land_area()
    df = pooled.merge(land, on=["state", "county_name"], how="left")

    df["density"] = df["total_population"] / df["sqmi"]
    df = df[df["density"] > 0].copy()
    df["log_density"] = np.log(df["density"])
    df["log_capture_ratio"] = np.log((df["obdb_count"] + 0.5) / (df["licensee_count"] + 0.5))

    model_df = df[df["licensee_count"] > 0].copy()
    print(f"Counties in model: {len(model_df)}\n")

    print("=" * 70)
    print("Fixed effects: log_capture_ratio ~ log_density + C(state)")
    print("=" * 70)
    fe = smf.ols("log_capture_ratio ~ log_density + C(state)", data=model_df).fit()
    print(fe.summary())

    print("\n" + "=" * 70)
    print("Pooled (no state term): log_capture_ratio ~ log_density")
    print("=" * 70)
    pooled_model = smf.ols("log_capture_ratio ~ log_density", data=model_df).fit()
    print(pooled_model.summary())

    print("\n" + "=" * 70)
    print("Random intercept: log_capture_ratio ~ log_density + (1|state)")
    print("=" * 70)
    mm = smf.mixedlm("log_capture_ratio ~ log_density", model_df, groups=model_df["state"]).fit()
    print(mm.summary())
    print(f"\nBetween-state random-effect variance: {mm.cov_re.iloc[0, 0]:.4f}")

    print("\n" + "=" * 70)
    print("Statewide pooled capture rates")
    print("=" * 70)
    by_state = model_df.groupby("state").agg(obdb=("obdb_count", "sum"), licensee=("licensee_count", "sum"))
    by_state["capture_rate"] = by_state["obdb"] / by_state["licensee"]
    print(by_state)
    overall = model_df["obdb_count"].sum() / model_df["licensee_count"].sum()
    print(f"\nPooled capture rate across 4 states: {overall:.1%}")
    print(f"Mean log_density across model counties: {model_df['log_density'].mean():.4f}")

    df.to_parquet("data/processed/pooled_calibration_with_density.parquet", index=False)
    print("\nWrote data/processed/pooled_calibration_with_density.parquet")


if __name__ == "__main__":
    main()
