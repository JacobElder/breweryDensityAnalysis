"""Assemble the Nebraska county-level analysis dataset and run validation checkpoints 1-3.

Calibration state (following NC, MI, CO, OR, WA, TX, GA, WI, PA, IL, CA, NY, VA).
Liquor-registry source is breweries.sources.ne_liquor -- the Nebraska Liquor
Control Commission's own "Active License Roster" Excel export, filtered to
Class L (Craft Brewery License) (see that module's docstring for the full
inclusion-rule writeup, including why hard-cider producers are kept in and
why out-of-state Class T shipper rows are excluded).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, ne_liquor, obdb, osm

pd.set_option("display.width", 160)

# Checked https://www.brewersassociation.org/statistics-and-data/state-craft-beer-stats/
# on 2026-08-31 (single state-total lookup, no directory scrape): Nebraska has 65
# craft breweries, ranked #36 nationally.
BA_NE_TOTAL_2025 = 65  # checked 2026-08-31, single state-total lookup


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("Nebraska")
    df = obdb.apply_inclusion_rule(df, "obdb_ne")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_ne")
    geo = assign_geographies(df, "latitude", "longitude", "NE", "obdb_ne")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("NE")
    geo = assign_geographies(df, "lat", "lon", "NE", "osm_ne")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("NE")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("NE", "county")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "total_population", "adults_21plus"]]


def build_liquor_county_counts() -> pd.DataFrame:
    # ne_liquor already carries NLCC's own county column -- no geocoding/spatial
    # join needed (same precedent as il_liquor.py / pa_liquor.py / mi_lara.py).
    df = ne_liquor.county_counts()
    return df.rename(columns={"ne_liquor_count": "liquor_count"})


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

    out_path = Path("data/processed/ne_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} counties)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (Douglas/Omaha and Lancaster/Lincoln should dominate")
    print("absolute counts, but may rank lower per-capita due to population dilution)")
    print("=" * 70)
    top = df[df["adults_21plus"] >= 10_000].sort_values("obdb_rate_per_100k_21plus", ascending=False)
    print(top[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].head(15).to_string(index=False))
    print("\nRaw counts for Douglas County specifically:")
    print(df[df["county_name"] == "Douglas"][
        ["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count", "adults_21plus"]
    ].to_string(index=False))

    print("\n" + "=" * 70)
    print("CHECKPOINT 2/3: State rollup + cross-source agreement vs BA")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum()),
        ("NLCC (Class L Craft Brewery License, active)", df["liquor_count"].sum()),
        ("Brewers Association (2025)", BA_NE_TOTAL_2025),
    ]:
        capture = val / BA_NE_TOTAL_2025 * 100
        print(f"{label:62s} {val:5d}   ({capture:5.1f}% of BA total)")

    print("\nTop 15 counties, all four sources side by side:")
    cmp = df.sort_values("liquor_count", ascending=False).head(15)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count"]].to_string(index=False))

    print(f"\nRaw OBDB capture rate (obdb_count.sum() / liquor_count.sum()): "
          f"{df['obdb_count'].sum()} / {df['liquor_count'].sum()} = "
          f"{df['obdb_count'].sum() / df['liquor_count'].sum() * 100:.1f}%")


if __name__ == "__main__":
    main()
