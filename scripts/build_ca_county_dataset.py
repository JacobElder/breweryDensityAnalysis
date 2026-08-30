"""Assemble the California county-level analysis dataset and run validation checkpoints 1-3.

Tenth calibration state (after NC, MI, CO, OR, WA, TX, GA, WI, PA). California is
the largest craft-beer state by brewery count, so face validity is checked
against San Diego County (reputed #1 craft-beer county in the US), Sonoma/Napa
(wine country, but Sonoma has a substantial brewery scene while Napa does not —
report what's actually there rather than forcing a wine-country prior), and the
Bay Area counties (Alameda, San Francisco, Santa Clara, Contra Costa, San Mateo,
Marin).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, ca_abc, cbp, obdb, osm

pd.set_option("display.width", 160)

BA_CA_TOTAL_2025 = 939  # checked 2026-08-30, single state-total lookup


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("California")
    df = obdb.apply_inclusion_rule(df, "CA")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_ca")
    geo = assign_geographies(df, "latitude", "longitude", "CA", "obdb_ca")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("CA")
    geo = assign_geographies(df, "lat", "lon", "CA", "osm_ca")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("CA")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("CA", "county")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "total_population", "adults_21plus"]]


def build_liquor_county_counts() -> pd.DataFrame:
    # ca_abc carries "Prem County" directly on every row — no lat/lon spatial
    # join needed for county-level rollups (unlike GA/PA, which lack a county
    # field and must geocode addresses first).
    return ca_abc.county_counts().rename(columns={"ca_abc_count": "liquor_count"})


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

    out_path = Path("data/processed/ca_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} counties)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (San Diego, Sonoma/Napa, Bay Area)")
    print("=" * 70)
    top = df[df["adults_21plus"] >= 50_000].sort_values("obdb_rate_per_100k_21plus", ascending=False)
    print(top[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].head(15).to_string(index=False))

    print("\nSan Diego / Sonoma / Napa / Bay Area counties, all sources:")
    watch = ["San Diego", "Sonoma", "Napa", "Alameda", "San Francisco", "Santa Clara",
             "Contra Costa", "San Mateo", "Marin"]
    watch_df = df[df["county_name"].isin(watch)]
    print(watch_df[["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count",
                     "adults_21plus", "obdb_rate_per_100k_21plus"]].to_string(index=False))

    print("\n" + "=" * 70)
    print("CHECKPOINT 2/3: State rollup + cross-source agreement vs BA")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum()),
        ("CA ABC (Type 01 + 23 + 75, active issued licenses)", df["liquor_count"].sum()),
        ("Brewers Association (2025)", BA_CA_TOTAL_2025),
    ]:
        capture = val / BA_CA_TOTAL_2025 * 100
        print(f"{label:62s} {val:5d}   ({capture:5.1f}% of BA total)")

    print("\nTop 15 counties, all four sources side by side:")
    cmp = df.sort_values("liquor_count", ascending=False).head(15)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count"]].to_string(index=False))


if __name__ == "__main__":
    main()
