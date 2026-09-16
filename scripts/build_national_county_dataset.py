"""Assemble the national county-level analysis dataset: brewery counts, ACS
denominators, covariates, and the capture-rate correction.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from breweries.capture_rate_model import CAPTURE_BASIS, apply_correction
from breweries.sources import acs, covariates
from breweries.state_fips import STATE_FIPS_ALL

FIPS_TO_ABBR = {v: k for k, v in STATE_FIPS_ALL.items()}


def main() -> None:
    # The HYGIENE-PASS output, not the raw geocode: non-brewery records that
    # passed OBDB's own type filter (wineries, cideries, meaderies typed
    # "micro"), duplicate entries for one physical location, and county
    # misassignments from street-name collisions are all resolved there.
    # See scripts/apply_obdb_hygiene.py and src/breweries/obdb_hygiene.py.
    geocoded = pd.read_parquet("data/processed/obdb_us_geocoded_clean.parquet")
    geocoded["county_geoid"] = geocoded["county_geoid"].astype("string").str.zfill(5)

    # Ungeocoded records are excluded (they have no county to be counted in),
    # but say so out loud -- this was previously a bare dropna() that removed
    # 3.1% of records without ever surfacing the number.
    n_ungeocoded = int(geocoded["county_geoid"].isna().sum())
    if n_ungeocoded:
        print(f"NOTE: {n_ungeocoded} records ({n_ungeocoded / len(geocoded):.1%}) have no county "
              "assignment and are excluded from county counts; they are itemized in "
              "data/processed/obdb_hygiene_report.csv")
    counts = geocoded.dropna(subset=["county_geoid"]).groupby("county_geoid").size().rename("obdb_count")

    # UNION COUNT as the modelling numerator. Against the 18 state registries
    # with trustworthy ground truth the union is a median 13.9% from truth
    # versus OBDB's 34.6% (methods memo 18.12), and it ranks counties more like
    # the independent CBP establishment count than OBDB does (18.15). The
    # capture rates in capture_rate_model were re-derived on this same
    # numerator FIRST -- see 18.13 for why that order is not optional.
    union_path = Path("data/processed/us_county_union_counts.parquet")
    union_counts = None
    if union_path.exists():
        u = pd.read_parquet(union_path)
        union_counts = u.set_index("county_geoid")["union_count"].rename("union_count")
        print(f"Union counts available for {len(union_counts):,} counties "
              f"({int(union_counts.sum()):,} breweries)")
    else:
        print("NOTE: no union count file; falling back to OBDB-only counts.")

    acs_county = acs.load_national("county")
    acs_county["county_geoid"] = acs_county["state"].astype(str).str.zfill(2) + acs_county["county"].astype(str).str.zfill(3)
    acs_county["county_name"] = acs_county["NAME"].str.split(",").str[0]
    acs_county["state_abbr"] = acs_county["state"].astype(str).str.zfill(2).map(FIPS_TO_ABBR)

    df = acs_county[["county_geoid", "county_name", "state_abbr", "total_population", "adults_21plus"]].copy()
    df = df.merge(counts, on="county_geoid", how="left")
    df["obdb_count"] = df["obdb_count"].fillna(0).astype(int)
    if union_counts is not None:
        df = df.merge(union_counts, on="county_geoid", how="left")
        # Counties absent from the union file have no records in EITHER source,
        # so their union count equals their (zero) OBDB count.
        df["union_count"] = df["union_count"].fillna(df["obdb_count"]).astype(int)
    else:
        df["union_count"] = df["obdb_count"]

    covar = covariates.load_county_covariates()
    df = df.merge(
        covar[["county_geoid", "median_household_income", "median_age",
               "college_enrollment_share", "tourism_estab", "sqmi", "pop_growth_pct",
               "unemployment_rate", "median_gross_rent"]],
        on="county_geoid", how="left",
    )
    df["density_per_sqmi"] = df["total_population"] / df["sqmi"]
    df["tourism_estab_per_10k"] = df["tourism_estab"] / df["total_population"] * 10_000
    # Winsorize at the 99th percentile: a tiny numerator over a tiny
    # population denominator produces per-10k ratios with no real-world
    # meaning (e.g. Mineral County, CO: 12 tourism establishments / 640
    # people = 164.6, vs. a national mean of 3.9 and 99th percentile of
    # ~34.8 -- 42 counties, almost all remote Alaska boroughs or tiny
    # mountain/lake counties, sit 10-50x past the 99th percentile). Found
    # because the combined spatial+covariate model (fit_combined_spatial_
    # covariate_model.py) fed these uncapped values into a log-linear
    # predictor and produced nonsensical >100/100k brewery-rate estimates
    # for exactly these counties -- an out-of-distribution extrapolation
    # this covariate was silently capable of producing in Model B too
    # (undetected there only because Model B's reported tables are always
    # population-floored at 50k adults, which happens to exclude every one
    # of these tiny counties). Capping preserves the real tourism signal
    # for the other ~99% of counties while preventing a few small-
    # denominator artifacts from dominating a linear model's fitted values.
    _TOURISM_CAP = df["tourism_estab_per_10k"].quantile(0.99)
    _n_capped = (df["tourism_estab_per_10k"] > _TOURISM_CAP).sum()
    df["tourism_estab_per_10k"] = df["tourism_estab_per_10k"].clip(upper=_TOURISM_CAP)
    print(f"Winsorized tourism_estab_per_10k at 99th percentile ({_TOURISM_CAP:.2f}): "
          f"{_n_capped} counties capped")

    # Apply the capture-rate correction model (see capture_rate_model.py):
    # calibrated states get their empirical rate, everyone else gets the pooled
    # rate + density adjustment with a wide uncertainty interval.
    # NUMERATOR IS DERIVED FROM THE CAPTURE BASIS, never chosen separately.
    # Mismatching them breaks in both directions: union counts with OBDB-basis
    # rates double-corrects (18.13), OBDB counts with union-basis rates
    # under-corrects. Tying them here makes the pairing structural rather than
    # a thing someone has to remember.
    numerator_col = "union_count" if CAPTURE_BASIS == "union" else "obdb_count"
    print(f"Capture basis '{CAPTURE_BASIS}' -> correcting {numerator_col}")
    log_density = np.log(df["density_per_sqmi"].clip(lower=0.1))
    corrections = [
        apply_correction(getattr(row, numerator_col), row.state_abbr, ld)
        for row, ld in zip(df.itertuples(), log_density)
    ]
    df["capture_rate"] = [c["capture_rate"] for c in corrections]
    df["obdb_corrected"] = [c["corrected_estimate"] for c in corrections]
    df["correction_source"] = [c["source"] for c in corrections]

    df["obdb_rate_per_100k_21plus"] = df["obdb_count"] / df["adults_21plus"] * 100_000
    df["union_rate_per_100k_21plus"] = df["union_count"] / df["adults_21plus"] * 100_000
    df["corrected_rate_per_100k_21plus"] = df["obdb_corrected"] / df["adults_21plus"] * 100_000

    out_path = Path("data/processed/us_county_analysis.parquet")
    df.to_parquet(out_path, index=False)
    print(f"Wrote {out_path} ({len(df)} counties)")
    print(f"Missing covariates: income={df['median_household_income'].isna().sum()}, "
          f"age={df['median_age'].isna().sum()}, density={df['density_per_sqmi'].isna().sum()}, "
          f"pop_growth_pct={df['pop_growth_pct'].isna().sum()}, "
          f"unemployment_rate={df['unemployment_rate'].isna().sum()}, "
          f"median_gross_rent={df['median_gross_rent'].isna().sum()}")
    print(f"Total OBDB breweries assigned to a county: {df['obdb_count'].sum()}")
    print(f"Total UNION breweries (modelling numerator): {df['union_count'].sum()}")


if __name__ == "__main__":
    main()
