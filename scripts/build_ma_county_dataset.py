"""Assemble the Massachusetts county-level analysis dataset and run
validation checkpoints 1-3.

Calibration state. Liquor-registry source is breweries.sources.ma_liquor --
the Alcoholic Beverages Control Commission's own "ABCC Active State
Licenses" export, linked from its own Active Licenses page. See that
module's docstring for the full inclusion-rule writeup (Farmer Brewery +
Pub Brewery license classes, the Manufacturer-class companion-license
exclusion, and why the "About to Expire" EXP_STATUS label is not used as a
filter).

Massachusetts has no independent-city/same-named-county collision (all 14
county-equivalents are ordinary counties, unlike VA/MO) and the ma_liquor
export carries no county field at all, only city/zip -- so, like GA DOR and
PA PLCB, records are geocoded via the Census Geocoder address fallback and
spatially joined to county polygons, the same path OBDB/OSM already use for
missing coordinates.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, ma_liquor, obdb, osm

pd.set_option("display.width", 160)

# Checked https://www.brewersassociation.org/statistics-and-data/state-craft-beer-stats/
# on 2026-08-31 (single state-total lookup, no directory scrape): Massachusetts has
# 209 craft breweries.
BA_MA_TOTAL_2025 = 209  # checked 2026-08-31, single state-total lookup


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("Massachusetts")
    df = obdb.apply_inclusion_rule(df, "obdb_ma")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_ma")
    geo = assign_geographies(df, "latitude", "longitude", "MA", "obdb_ma")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("MA")
    geo = assign_geographies(df, "lat", "lon", "MA", "osm_ma")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("MA")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("MA", "county")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "total_population", "adults_21plus"]]


def build_liquor_county_counts() -> pd.DataFrame:
    df = ma_liquor.load()
    df = fill_missing_coords(df, "ma_liquor_id", "lat", "lon", "street_address", "city",
                              "state", "zip", "ma_liquor")
    geo = assign_geographies(df, "lat", "lon", "MA", "ma_liquor")
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

    out_path = Path("data/processed/ma_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} counties)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (Suffolk/Boston, Middlesex, and the Pioneer")
    print("Valley/Worcester craft scene should rank prominently)")
    print("=" * 70)
    top = df[df["adults_21plus"] >= 50_000].sort_values("obdb_rate_per_100k_21plus", ascending=False)
    print(top[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].head(14).to_string(index=False))

    print("\n" + "=" * 70)
    print("CHECKPOINT 2/3: State rollup + cross-source agreement vs BA")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum()),
        ("ABCC (Farmer Brewery + Pub Brewery state licenses)", df["liquor_count"].sum()),
        ("Brewers Association (2025)", BA_MA_TOTAL_2025),
    ]:
        capture = val / BA_MA_TOTAL_2025 * 100
        print(f"{label:62s} {val:5d}   ({capture:5.1f}% of BA total)")

    print("\nAll 14 counties, all four sources side by side:")
    cmp = df.sort_values("liquor_count", ascending=False)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count"]].to_string(index=False))

    print("\nRaw capture rate (aggregated): "
          f"{df['liquor_count'].sum()} / {df['obdb_count'].sum()} obdb = "
          f"{df['liquor_count'].sum() / df['obdb_count'].sum() * 100:.1f}%")


if __name__ == "__main__":
    main()
