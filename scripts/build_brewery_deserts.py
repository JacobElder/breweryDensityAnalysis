"""Brewery deserts: the symmetric inverse of every other ranking artifact in this
project. Everything built so far (us_county_shrunken_rankings.parquet,
us_county_corrected_shrunken_rankings.parquet, us_county_raw_vs_corrected_rankings.csv,
the choropleths, the top-50 tables) surfaces counties with unexpectedly HIGH brewery
density. Nobody has sorted the same data the other way: large-population counties
with unexpectedly LOW capture-rate-corrected, shrunken density -- i.e. candidate
"brewery deserts", areas of unmet market potential.

This is deliberately NOT new modeling. It reuses the same population floor
(POPULATION_FLOOR = 50,000 adults 21+) and the same empirical-Bayes-shrunken,
capture-rate-corrected rate already computed in scripts/build_corrected_rankings.py
(eb_posterior_rate_per_100k_corrected). The floor matters here for the same reason it
matters for the high-density rankings: a tiny county with 0 observed breweries is
"low density" almost by construction (shrinkage pulls its posterior toward the
national mean, but with so little exposure the estimate is still noisy and not a
meaningful "desert" claim). Restricting to counties with >= 50,000 adults 21+ keeps
desert claims held to the same evidentiary bar as the high-density claims elsewhere
in this project.

Two views are produced, because they answer different questions:
  1. Pure bottom-of-ranking: among population-floored counties, sorted ascending by
     corrected-shrunken rate. This is "worst density, full stop" -- but a 51,000-adult
     county at the bottom is a much smaller commercial opportunity than a 1M-adult
     county at the bottom.
  2. Population-weighted cross-cut: among the TOP_N_LARGEST counties by adults_21plus
     nationally, sorted ascending by corrected-shrunken rate. This answers "where is
     the biggest absolute-population underserved market", which is arguably the more
     actionable business question.

Both raw-shrunken and corrected-shrunken rates are used to build independent desert
lists, and the two are compared (rank correlation + top-20 overlap) as a robustness
check, mirroring how build_corrected_rankings.py treats the raw ranking as a baseline
for the corrected one.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CORRECTED_PATH = "data/processed/us_county_corrected_shrunken_rankings.parquet"
RAW_PATH = "data/processed/us_county_shrunken_rankings.parquet"
POPULATION_FLOOR = 50_000
TOP_N_LARGEST = 100  # for the population-weighted "biggest market, lowest density" cross-cut
OUT_PATH = "data/processed/us_county_brewery_deserts.csv"

COVARIATE_COLS = ["median_household_income", "median_age", "college_enrollment_share", "tourism_estab_per_10k"]

# Simple Census Bureau 4-region mapping by state abbreviation, used only for the
# descriptive "are deserts regionally clustered?" check below -- not stored anywhere
# else in this project, so it's inlined here rather than invented as a new shared module.
CENSUS_REGION = {
    "CT": "Northeast", "ME": "Northeast", "MA": "Northeast", "NH": "Northeast", "RI": "Northeast",
    "VT": "Northeast", "NJ": "Northeast", "NY": "Northeast", "PA": "Northeast",
    "IL": "Midwest", "IN": "Midwest", "MI": "Midwest", "OH": "Midwest", "WI": "Midwest",
    "IA": "Midwest", "KS": "Midwest", "MN": "Midwest", "MO": "Midwest", "NE": "Midwest",
    "ND": "Midwest", "SD": "Midwest",
    "DE": "South", "FL": "South", "GA": "South", "MD": "South", "NC": "South", "SC": "South",
    "VA": "South", "DC": "South", "WV": "South", "AL": "South", "KY": "South", "MS": "South",
    "TN": "South", "AR": "South", "LA": "South", "OK": "South", "TX": "South",
    "AZ": "West", "CO": "West", "ID": "West", "MT": "West", "NV": "West", "NM": "West",
    "UT": "West", "WY": "West", "AK": "West", "CA": "West", "HI": "West", "OR": "West", "WA": "West",
}

pd.set_option("display.width", 160)
pd.set_option("display.max_colwidth", 30)


def load_floored(path: str, rate_col: str) -> pd.DataFrame:
    """Population-floored counties from a shrunken-rankings parquet, with an
    ascending-density rank computed WITHIN that floored universe (rank 1 = LOWEST
    density = biggest desert). This mirrors the rank semantics of
    us_county_raw_vs_corrected_rankings.csv's corrected_rank column (which ranks
    descending, rank 1 = highest density) just flipped, so "corrected_rank" values
    from that file and "desert_rank" values here are directly comparable
    (desert_rank == floored_n + 1 - corrected_rank).
    """
    df = pd.read_parquet(path)
    floored = df[df["adults_21plus"] >= POPULATION_FLOOR].copy()
    floored["desert_rank"] = floored[rate_col].rank(ascending=True, method="min").astype(int)
    floored["national_rank"] = df.set_index("county_geoid").loc[floored["county_geoid"], rate_col].rank(
        ascending=True, method="min"
    ).astype(int).values
    return floored


def build_note(row: pd.Series, floored_n: int) -> str:
    return (
        f"Ranked {row['desert_rank']} of {floored_n} population-floored counties "
        f"(adults 21+ >= {POPULATION_FLOOR:,}), lowest corrected density first. "
        f"{row['obdb_count']:.0f} OBDB-observed breweries for {row['adults_21plus']:,.0f} adults 21+ "
        f"({row['eb_posterior_rate_per_100k_corrected']:.2f} corrected breweries per 100k, shrunken)."
    )


def report_covariate_comparison(label: str, subset: pd.DataFrame, universe: pd.DataFrame) -> None:
    print(f"\n  Covariate means -- {label} vs. all {len(universe)} population-floored counties:")
    for col in COVARIATE_COLS:
        s_mean, u_mean = subset[col].mean(), universe[col].mean()
        print(f"    {col:28s} desert={s_mean:>12,.3f}   floored-universe={u_mean:>12,.3f}")
    print("\n  Region counts (Census 4-region):")
    print("    " + subset["region"].value_counts().to_string().replace("\n", "\n    "))


def main() -> None:
    print("=" * 70)
    print("Brewery deserts: large-population counties, lowest corrected density")
    print("=" * 70)

    corrected = load_floored(CORRECTED_PATH, "eb_posterior_rate_per_100k_corrected")
    raw = load_floored(RAW_PATH, "eb_posterior_rate_per_100k")
    floored_n = len(corrected)
    assert len(raw) == floored_n, "raw and corrected floored universes should match in size"

    corrected["region"] = corrected["state_abbr"].map(CENSUS_REGION)

    # --- View 1: pure bottom-of-ranking, corrected -------------------------------
    deserts = corrected.sort_values("desert_rank").copy()
    deserts["note"] = deserts.apply(lambda r: build_note(r, floored_n), axis=1)

    print(f"\nTop 20 brewery deserts (bottom of the ranking, corrected-shrunken rate, "
          f"population >= {POPULATION_FLOOR:,} adults 21+, n={floored_n}):")
    print(deserts.head(20)[[
        "county_name", "state_abbr", "adults_21plus", "obdb_count",
        "eb_posterior_rate_per_100k_corrected", "desert_rank", "national_rank",
    ]].to_string(index=False))

    # --- View 2: population-weighted cross-cut ------------------------------------
    largest = corrected.sort_values("adults_21plus", ascending=False).head(TOP_N_LARGEST).copy()
    largest_deserts = largest.sort_values("eb_posterior_rate_per_100k_corrected", ascending=True)

    print(f"\nTop 20 of the {TOP_N_LARGEST} LARGEST counties nationally by adults_21plus, "
          "sorted by lowest corrected density (biggest absolute-population opportunity):")
    print(largest_deserts.head(20)[[
        "county_name", "state_abbr", "adults_21plus", "obdb_count",
        "eb_posterior_rate_per_100k_corrected", "desert_rank", "national_rank",
    ]].to_string(index=False))

    # --- Robustness: raw vs. corrected desert lists -------------------------------
    raw_small = raw[["county_geoid", "desert_rank"]].rename(columns={"desert_rank": "desert_rank_raw"})
    cmp = deserts.merge(raw_small, on="county_geoid", how="left")
    rank_corr = cmp["desert_rank"].corr(cmp["desert_rank_raw"], method="spearman")

    corrected_bottom20 = set(cmp.sort_values("desert_rank").head(20)["county_geoid"])
    raw_bottom20 = set(cmp.sort_values("desert_rank_raw").head(20)["county_geoid"])
    overlap = corrected_bottom20 & raw_bottom20

    print("\n" + "=" * 70)
    print("Raw vs. corrected desert-list robustness check")
    print("=" * 70)
    print(f"Spearman rank correlation (corrected desert_rank vs raw desert_rank), "
          f"n={floored_n}: {rank_corr:.3f}")
    print(f"Bottom-20 overlap: {len(overlap)}/20 counties agree between raw and corrected lists")
    only_corrected = corrected_bottom20 - raw_bottom20
    if only_corrected:
        rows = cmp[cmp["county_geoid"].isin(only_corrected)][
            ["county_name", "state_abbr", "desert_rank", "desert_rank_raw"]
        ]
        print("\nCounties in CORRECTED bottom-20 but NOT in raw bottom-20 "
              "(correction pushed them further down / up out of contention):")
        print(rows.to_string(index=False))

    # --- Optional covariate / regional pattern check ------------------------------
    print("\n" + "=" * 70)
    print("Demographic / regional pattern check (bottom-50 desert counties)")
    print("=" * 70)
    bottom50 = deserts.head(50)
    report_covariate_comparison("bottom-50 deserts", bottom50, corrected)

    # --- Write output --------------------------------------------------------------
    out_cols = [
        "county_geoid", "county_name", "state_abbr", "adults_21plus", "obdb_count",
        "eb_posterior_rate_per_100k_corrected", "desert_rank", "national_rank", "note",
    ]
    out = deserts[out_cols].rename(columns={"desert_rank": "corrected_rank"})
    out["in_top100_by_population"] = out["county_geoid"].isin(largest["county_geoid"])
    out.to_csv(OUT_PATH, index=False)
    print(f"\nWrote {OUT_PATH} ({len(out)} counties, population >= {POPULATION_FLOOR:,} adults 21+, "
          f"sorted ascending by corrected-shrunken rate -- most extreme desert first).\n"
          f"corrected_rank is out of the {floored_n}-county population-floored universe "
          f"(rank 1 = lowest density / biggest desert). national_rank is the same county's "
          f"ascending-density rank out of the full {len(pd.read_parquet(CORRECTED_PATH))}-county "
          f"national universe, included for context since most of that universe is small counties "
          f"excluded here by the population floor.")


if __name__ == "__main__":
    main()
