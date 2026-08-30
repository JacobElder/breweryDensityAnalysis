"""Assemble the Washington county-level analysis dataset and run validation checkpoints 1-3.

Fifth calibration state (after NC, MI, CO, OR). WA's WSLCB brewery licensee
list (src/breweries/sources/wa_liquor.py) has no lat/lon or county field —
only street/city/state/zip — so, unlike CO/OR's liquor sources, it is run
through breweries.geocode.fill_missing_coords (the same Census Geocoder
fallback path OBDB uses) before the county spatial join.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, obdb, osm, wa_liquor

pd.set_option("display.width", 160)

# Brewers Association state-craft-beer-stats page, single state-total lookup only
# (https://www.brewersassociation.org/statistics-and-data/state-craft-beer-stats/):
# Washington - "438 Craft Breweries (Ranks 4th)".
BA_WA_TOTAL_2025 = 438  # checked 2026-08-30, single state-total lookup


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("Washington")
    df = obdb.apply_inclusion_rule(df, "WA")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_wa")
    geo = assign_geographies(df, "latitude", "longitude", "WA", "obdb_wa")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("WA")
    geo = assign_geographies(df, "lat", "lon", "WA", "osm_wa")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("WA")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("WA", "county")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "total_population", "adults_21plus"]]


def build_liquor_county_counts() -> pd.DataFrame:
    df = wa_liquor.load()
    df = fill_missing_coords(df, "license_number", "lat", "lon", "street_address", "city",
                              "state", "zip", "wa_liquor")
    geo = assign_geographies(df, "lat", "lon", "WA", "wa_liquor")
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

    out_path = Path("data/processed/wa_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} counties)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (King/Seattle, Whatcom/Bellingham should rank high)")
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
        ("WSLCB (Washington Domestic and Microbreweries, type 326)", df["liquor_count"].sum()),
        ("Brewers Association (2025)", BA_WA_TOTAL_2025),
    ]:
        capture = val / BA_WA_TOTAL_2025 * 100
        print(f"{label:62s} {val:5d}   ({capture:5.1f}% of BA total)")

    print("\nTop 10 counties, all four sources side by side:")
    cmp = df.sort_values("liquor_count", ascending=False).head(10)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count"]].to_string(index=False))


if __name__ == "__main__":
    main()
