"""Assemble the New York county-level analysis dataset and run validation checkpoints 1-3.

Tenth calibration state (after NC, MI, CO, OR, WA, TX, GA, WI, PA). New York's
State Liquor Authority "Current Liquor Authority Active Licenses" dataset
(breweries.sources.ny_sla, data.ny.gov id 9s3h-dpkz) is a genuine Socrata bulk
API — the handoff brief's originally-suggested starting point for this state,
and it panned out. Almost every row already carries a lat/lon point and a
county field directly; only 3 of 709 raw license rows needed the Census
Geocoder fallback, and all 3 come back "No_Match" from the Census Geocoder
itself (rural hamlet addresses), so ny_sla.load() falls back to keeping those
rows uncoordinated rather than inventing a location — see the module docstring
and the try/except note in ny_sla.load() for why.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, ny_sla, obdb, osm

pd.set_option("display.width", 160)

BA_NY_TOTAL_2025 = 525  # checked 2026-08-30, single state-total lookup


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("New York")
    df = obdb.apply_inclusion_rule(df, "NY")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_ny")
    geo = assign_geographies(df, "latitude", "longitude", "NY", "obdb_ny")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("NY")
    geo = assign_geographies(df, "lat", "lon", "NY", "osm_ny")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("NY")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("NY", "county")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "total_population", "adults_21plus"]]


def build_liquor_county_counts() -> pd.DataFrame:
    df = ny_sla.load()
    geo = assign_geographies(df, "lat", "lon", "NY", "ny_sla")
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

    out_path = Path("data/processed/ny_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} counties)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (Finger Lakes / Hudson Valley / NYC boroughs / Buffalo)")
    print("=" * 70)
    top = df[df["adults_21plus"] >= 50_000].sort_values("obdb_rate_per_100k_21plus", ascending=False)
    print(top[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].head(15).to_string(index=False))

    print("\n" + "=" * 70)
    print("CHECKPOINT 2/3: State rollup + cross-source agreement vs BA")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum()),
        ("NY SLA (Brewer + Micro-Brewer + Farm Brewer + Restaurant Brewer, deduped by address)", df["liquor_count"].sum()),
        ("Brewers Association (2025)", BA_NY_TOTAL_2025),
    ]:
        capture = val / BA_NY_TOTAL_2025 * 100
        print(f"{label:88s} {val:5d}   ({capture:5.1f}% of BA total)")

    print("\nTop 15 counties, all four sources side by side:")
    cmp = df.sort_values("liquor_count", ascending=False).head(15)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count"]].to_string(index=False))


if __name__ == "__main__":
    main()
