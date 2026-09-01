"""Assemble the Washington DC "county-level" (single-jurisdiction) analysis dataset and
run validation checkpoints 1-3.

Calibration state-equivalent (following NC, MI, CO, OR, WA, TX, GA, WI, PA, IL, CA, NY,
VA, KY, FL, CT, WV, WY). Liquor-registry source is breweries.sources.dc_abra -- DC's own
open-data-portal GIS layer for ABCA licenses (see that module's docstring for the full
inclusion-rule writeup: DC has no dedicated brewery license class, so brewing locations
are identified via TYPE == 'Manufacturer' (individually classified vs. spirits) OR
BREW_PUB == 'CHECKED').

DC has no counties -- per src/breweries/state_fips.py, the whole District is one Census
county-equivalent, "District of Columbia" (FIPS 11001). This script therefore produces a
single-row "county" table rather than the usual multi-county breakdown; Checkpoint 1
(top-county face validity) is not meaningful here and is skipped.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, dc_abra, obdb, osm

pd.set_option("display.width", 160)

# Checked https://www.brewersassociation.org/statistics-and-data/state-craft-beer-stats/
# on 2026-08-31 (single state-total lookup, no directory scrape): Washington DC has 11
# craft breweries (ranks 51st, last).
BA_DC_TOTAL_2025 = 11  # checked 2026-08-31, single state-total lookup


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("District of Columbia")
    df = obdb.apply_inclusion_rule(df, "obdb_dc")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_dc")
    geo = assign_geographies(df, "latitude", "longitude", "DC", "obdb_dc")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("DC")
    geo = assign_geographies(df, "lat", "lon", "DC", "osm_dc")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("DC")
    df["county_name"] = df["NAME"].str.split(",").str[0]
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("DC", "county")
    df["county_name"] = df["NAME"].str.split(",").str[0]
    return df[["county_name", "total_population", "adults_21plus"]]


def build_liquor_county_counts() -> pd.DataFrame:
    # dc_abra already carries a single county_name ("District of Columbia") for every
    # row -- no geocoding/spatial join needed.
    df = dc_abra.county_counts()
    return df.rename(columns={"dc_abra_count": "liquor_count"})


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

    out_path = Path("data/processed/dc_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} row -- DC is a single county-equivalent, no "
          "multi-county breakdown)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity -- N/A, DC has only one county-equivalent jurisdiction")
    print("=" * 70)
    print(df[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].to_string(index=False))

    print("\n" + "=" * 70)
    print("CHECKPOINT 2/3: Jurisdiction rollup + cross-source agreement vs BA")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum()),
        ("DC ABCA (Manufacturer + Brew Pub flag, opendata.dc.gov GIS layer)", df["liquor_count"].sum()),
        ("Brewers Association (2025)", BA_DC_TOTAL_2025),
    ]:
        capture = val / BA_DC_TOTAL_2025 * 100
        print(f"{label:66s} {val:5d}   ({capture:5.1f}% of BA total)")

    obdb_total = int(df["obdb_count"].sum())
    liquor_total = int(df["liquor_count"].sum())
    print(f"\nRaw capture rate (obdb_count / liquor_count): {obdb_total} / {liquor_total} = "
          f"{obdb_total / liquor_total * 100:.1f}%")


if __name__ == "__main__":
    main()
