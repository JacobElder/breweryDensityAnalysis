"""Assemble the Oregon county-level analysis dataset and run validation checkpoints 1-3.

Fourth calibration state (after NC, MI, CO). Also exercises OLCC's built-in
primary-vs-additional-location distinction as the satellite-taproom sensitivity
check the project handoff asks for.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, obdb, or_olcc, osm

pd.set_option("display.width", 160)

BA_OR_TOTAL_2025 = 297  # checked 2026-08-30, single state-total lookup


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("Oregon")
    df = obdb.apply_inclusion_rule(df, "OR")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_or")
    geo = assign_geographies(df, "latitude", "longitude", "OR", "obdb_or")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("OR")
    geo = assign_geographies(df, "lat", "lon", "OR", "osm_or")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("OR")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("OR", "county")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "total_population", "adults_21plus"]]


def main() -> None:
    obdb_counts = build_obdb_county_counts()
    osm_counts = build_osm_county_counts()
    cbp_counts = build_cbp_county_counts()
    acs_denom = build_acs_county_denominators()
    olcc_primary = or_olcc.county_counts(include_additional_locations=False).rename(
        columns={"olcc_count": "olcc_primary_count"})
    olcc_with_satellites = or_olcc.county_counts(include_additional_locations=True).rename(
        columns={"olcc_count": "olcc_with_satellites_count"})

    df = acs_denom.merge(obdb_counts, on="county_name", how="left")
    df = df.merge(osm_counts, on="county_name", how="left")
    df = df.merge(cbp_counts, on="county_name", how="left")
    df = df.merge(olcc_primary, on="county_name", how="left")
    df = df.merge(olcc_with_satellites, on="county_name", how="left")

    for col in ["obdb_count", "osm_count", "cbp_estab", "olcc_primary_count", "olcc_with_satellites_count"]:
        df[col] = df[col].fillna(0).astype(int)

    df["obdb_rate_per_100k_21plus"] = df["obdb_count"] / df["adults_21plus"] * 100_000

    out_path = Path("data/processed/or_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} counties)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (Bend / Deschutes County should rank very high)")
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
        ("OLCC primary licenses (satellites excluded, default)", df["olcc_primary_count"].sum()),
        ("OLCC incl. additional-location licenses (satellite sensitivity)", df["olcc_with_satellites_count"].sum()),
        ("Brewers Association (2025)", BA_OR_TOTAL_2025),
    ]:
        capture = val / BA_OR_TOTAL_2025 * 100
        print(f"{label:66s} {val:5d}   ({capture:5.1f}% of BA total)")

    print("\nSatellite-taproom sensitivity: primary vs incl.-satellites, top 10 counties")
    cmp = df.sort_values("olcc_with_satellites_count", ascending=False).head(10)
    cmp = cmp.copy()
    cmp["satellite_delta"] = cmp["olcc_with_satellites_count"] - cmp["olcc_primary_count"]
    print(cmp[["county_name", "obdb_count", "olcc_primary_count", "olcc_with_satellites_count",
               "satellite_delta"]].to_string(index=False))


if __name__ == "__main__":
    main()
