"""Assemble the national county-level analysis dataset: brewery counts, ACS
denominators, covariates, and the capture-rate correction.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from breweries.capture_rate_model import apply_correction
from breweries.sources import acs, covariates
from breweries.state_fips import STATE_FIPS_ALL

FIPS_TO_ABBR = {v: k for k, v in STATE_FIPS_ALL.items()}


def main() -> None:
    geocoded = pd.read_parquet("data/processed/obdb_us_geocoded.parquet")
    geocoded["county_geoid"] = geocoded["county_geoid"].str.zfill(5)
    counts = geocoded.dropna(subset=["county_geoid"]).groupby("county_geoid").size().rename("obdb_count")

    acs_county = acs.load_national("county")
    acs_county["county_geoid"] = acs_county["state"].astype(str).str.zfill(2) + acs_county["county"].astype(str).str.zfill(3)
    acs_county["county_name"] = acs_county["NAME"].str.split(",").str[0]
    acs_county["state_abbr"] = acs_county["state"].astype(str).str.zfill(2).map(FIPS_TO_ABBR)

    df = acs_county[["county_geoid", "county_name", "state_abbr", "total_population", "adults_21plus"]].copy()
    df = df.merge(counts, on="county_geoid", how="left")
    df["obdb_count"] = df["obdb_count"].fillna(0).astype(int)

    covar = covariates.load_county_covariates()
    df = df.merge(
        covar[["county_geoid", "median_household_income", "median_age",
               "college_enrollment_share", "tourism_estab", "sqmi", "pop_growth_pct"]],
        on="county_geoid", how="left",
    )
    df["density_per_sqmi"] = df["total_population"] / df["sqmi"]
    df["tourism_estab_per_10k"] = df["tourism_estab"] / df["total_population"] * 10_000

    # Apply the capture-rate correction model (see capture_rate_model.py):
    # calibrated states get their empirical rate, everyone else gets the pooled
    # rate + density adjustment with a wide uncertainty interval.
    log_density = np.log(df["density_per_sqmi"].clip(lower=0.1))
    corrections = [
        apply_correction(row.obdb_count, row.state_abbr, ld)
        for row, ld in zip(df.itertuples(), log_density)
    ]
    df["capture_rate"] = [c["capture_rate"] for c in corrections]
    df["obdb_corrected"] = [c["corrected_estimate"] for c in corrections]
    df["correction_source"] = [c["source"] for c in corrections]

    df["obdb_rate_per_100k_21plus"] = df["obdb_count"] / df["adults_21plus"] * 100_000
    df["corrected_rate_per_100k_21plus"] = df["obdb_corrected"] / df["adults_21plus"] * 100_000

    out_path = Path("data/processed/us_county_analysis.parquet")
    df.to_parquet(out_path, index=False)
    print(f"Wrote {out_path} ({len(df)} counties)")
    print(f"Missing covariates: income={df['median_household_income'].isna().sum()}, "
          f"age={df['median_age'].isna().sum()}, density={df['density_per_sqmi'].isna().sum()}, "
          f"pop_growth_pct={df['pop_growth_pct'].isna().sum()}")
    print(f"Total OBDB breweries assigned to a county: {df['obdb_count'].sum()}")


if __name__ == "__main__":
    main()
