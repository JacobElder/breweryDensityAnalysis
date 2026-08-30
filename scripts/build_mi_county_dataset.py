"""Assemble the Michigan county-level analysis dataset and run validation checkpoints 1-3.

Second calibration state (after NC), per the project handoff's suggested order.
Outputs data/processed/mi_county_analysis.csv.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, mi_lara, obdb, osm

pd.set_option("display.width", 160)

# Manually checked once against https://www.brewersassociation.org/statistics-and-data/
# state-craft-beer-stats/ on 2026-08-30 (single state-total lookup, no directory scrape).
BA_MI_TOTAL_2025 = 410


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("Michigan")
    df = obdb.apply_inclusion_rule(df, "MI")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_mi")
    geo = assign_geographies(df, "latitude", "longitude", "26", "mi_place_*.zip", "obdb_mi")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("MI")
    geo = assign_geographies(df, "lat", "lon", "26", "mi_place_*.zip", "osm_mi")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("MI")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("MI", "county")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "total_population", "adults_21plus"]]


def build_lara_county_counts() -> pd.DataFrame:
    df = mi_lara.county_counts()
    return df.rename(columns={"county": "county_name"})


def main() -> None:
    obdb_counts = build_obdb_county_counts()
    osm_counts = build_osm_county_counts()
    cbp_counts = build_cbp_county_counts()
    acs_denom = build_acs_county_denominators()
    lara_counts = build_lara_county_counts()

    df = acs_denom.merge(obdb_counts, on="county_name", how="left")
    df = df.merge(osm_counts, on="county_name", how="left")
    df = df.merge(cbp_counts, on="county_name", how="left")
    df = df.merge(lara_counts, on="county_name", how="left")

    for col in ["obdb_count", "osm_count", "cbp_estab", "lara_permit_count"]:
        df[col] = df[col].fillna(0).astype(int)

    df["obdb_rate_per_100k_21plus"] = df["obdb_count"] / df["adults_21plus"] * 100_000

    out_path = Path("data/processed/mi_county_analysis.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} counties)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (Kent/Grand Rapids should rank high)")
    print("=" * 70)
    top = df[df["adults_21plus"] >= 50_000].sort_values("obdb_rate_per_100k_21plus", ascending=False)
    print(top[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].head(10).to_string(index=False))

    print("\n" + "=" * 70)
    print("CHECKPOINT 2: State rollup vs Brewers Association")
    print("=" * 70)
    print(f"OBDB statewide total: {df['obdb_count'].sum()}")
    print(f"Brewers Association MI total (2025): {BA_MI_TOTAL_2025}")
    pct_diff = (df["obdb_count"].sum() - BA_MI_TOTAL_2025) / BA_MI_TOTAL_2025 * 100
    print(f"OBDB vs BA: {pct_diff:+.1f}%")

    print("\n" + "=" * 70)
    print("CHECKPOINT 3: Cross-source agreement + capture rate vs BA")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum()),
        ("MI LARA (active Micro Brewer + Brewer licenses)", df["lara_permit_count"].sum()),
        ("Brewers Association (2025)", BA_MI_TOTAL_2025),
    ]:
        capture = val / BA_MI_TOTAL_2025 * 100
        print(f"{label:62s} {val:5d}   ({capture:5.1f}% of BA total)")

    print("\nTop 10 counties, all four sources side by side:")
    cmp = df.sort_values("lara_permit_count", ascending=False).head(10)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab", "lara_permit_count"]].to_string(index=False))


if __name__ == "__main__":
    main()
