"""Assemble the Georgia county-level analysis dataset and run validation checkpoints 1-3.

Fifth calibration state (after NC, MI, CO, OR). Unlike CO/OR's Socrata APIs,
GA DOR's Active Alcohol Licenses export (breweries.sources.ga_dor) carries no
lat/lon or county field — only a single combined address string — so the GA
liquor counts route through the same Census Geocoder fallback
(breweries.geocode.fill_missing_coords) that OBDB already uses for
coordinate-less records, rather than through direct lat/lon like CO/OR.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, ga_dor, obdb, osm

pd.set_option("display.width", 160)

BA_GA_TOTAL_2025 = 186  # checked 2026-08-30, single state-total lookup


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("Georgia")
    df = obdb.apply_inclusion_rule(df, "GA")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_ga")
    geo = assign_geographies(df, "latitude", "longitude", "GA", "obdb_ga")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("GA")
    geo = assign_geographies(df, "lat", "lon", "GA", "osm_ga")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("GA")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("GA", "county")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "total_population", "adults_21plus"]]


def build_liquor_county_counts() -> pd.DataFrame:
    df = ga_dor.load()
    df = fill_missing_coords(df, "ga_dor_id", "lat", "lon", "street_address", "city",
                              "state", "zip", "ga_dor")
    geo = assign_geographies(df, "lat", "lon", "GA", "ga_dor")
    counts = geo.groupby("county_name", dropna=True).size().rename("liquor_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def main() -> None:
    obdb_counts = build_obdb_county_counts()
    osm_counts = build_osm_county_counts()
    cbp_counts = build_cbp_county_counts()
    acs_denom = build_acs_county_denominators()
    liquor_counts = build_liquor_county_counts()

    df = acs_denom.merge(obdb_counts, on="county_name", how="left")
    df = df.merge(osm_counts, on="county_name", how="left")
    df = df.merge(cbp_counts, on="county_name", how="left")
    df = df.merge(liquor_counts, on="county_name", how="left")

    for col in ["obdb_count", "osm_count", "cbp_estab", "liquor_count"]:
        df[col] = df[col].fillna(0).astype(int)

    df["obdb_rate_per_100k_21plus"] = df["obdb_count"] / df["adults_21plus"] * 100_000

    out_path = Path("data/processed/ga_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} counties)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (Fulton County / Atlanta's growing craft scene should rank high)")
    print("=" * 70)
    top = df[df["adults_21plus"] >= 50_000].sort_values("obdb_rate_per_100k_21plus", ascending=False)
    print(top[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].head(10).to_string(index=False))

    print("\n" + "=" * 70)
    print("CHECKPOINT 2/3: State rollup + cross-source agreement vs BA")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum()),
        ("GA DOR (BREWERY + BREWPUB, in-state addresses)", df["liquor_count"].sum()),
        ("Brewers Association (2025)", BA_GA_TOTAL_2025),
    ]:
        capture = val / BA_GA_TOTAL_2025 * 100
        print(f"{label:62s} {val:5d}   ({capture:5.1f}% of BA total)")

    print("\nTop 10 counties, all four sources side by side:")
    cmp = df.sort_values("liquor_count", ascending=False).head(10)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count"]].to_string(index=False))


if __name__ == "__main__":
    main()
