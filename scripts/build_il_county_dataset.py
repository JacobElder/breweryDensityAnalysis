"""Assemble the Illinois county-level analysis dataset and run validation checkpoints 1-3.

Calibration state (following NC, MI, CO, OR, WA, TX, GA, WI, PA). Liquor-registry
source is breweries.sources.il_liquor -- ILCC's own daily statewide license-export
CSV, linked from its FOIA Section 4 disclosure page (see that module's docstring
for the full inclusion-rule writeup, including why the export needed an
expiration-date "active" filter and how companion 3C/Class-1-2-3 licenses at the
same premises are deduplicated).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, il_liquor, obdb, osm

pd.set_option("display.width", 160)

# Checked https://www.brewersassociation.org/statistics-and-data/state-craft-beer-stats/
# on 2026-08-30 (single state-total lookup, no directory scrape): Illinois has 288
# craft breweries, ranked #13 nationally.
BA_IL_TOTAL_2025 = 288  # checked 2026-08-30, single state-total lookup


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("Illinois")
    df = obdb.apply_inclusion_rule(df, "obdb_il")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_il")
    geo = assign_geographies(df, "latitude", "longitude", "IL", "obdb_il")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("IL")
    geo = assign_geographies(df, "lat", "lon", "IL", "osm_il")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("IL")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("IL", "county")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "total_population", "adults_21plus"]]


def build_liquor_county_counts() -> pd.DataFrame:
    # il_liquor already carries ILCC's own county column -- no geocoding/spatial
    # join needed (same precedent as pa_liquor.py / mi_lara.py), see module docstring.
    df = il_liquor.county_counts()
    return df.rename(columns={"il_liquor_count": "liquor_count"})


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

    out_path = Path("data/processed/il_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} counties)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (Cook/Chicago should dominate absolute counts, but may")
    print("rank lower per-capita due to population dilution -- same pattern as other large-metro")
    print("counties already seen in this project)")
    print("=" * 70)
    top = df[df["adults_21plus"] >= 50_000].sort_values("obdb_rate_per_100k_21plus", ascending=False)
    print(top[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].head(15).to_string(index=False))
    print("\nRaw counts for Cook County specifically:")
    print(df[df["county_name"] == "Cook"][
        ["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count", "adults_21plus"]
    ].to_string(index=False))
    top_reset = top.reset_index(drop=True)
    if "Cook" in top_reset["county_name"].values:
        cook_rate_rank = int(top_reset.index[top_reset["county_name"] == "Cook"][0]) + 1
        print(f"\nCook County per-capita rank among counties with 50k+ adults 21+: {cook_rate_rank} of {len(top_reset)}")

    print("\n" + "=" * 70)
    print("CHECKPOINT 2/3: State rollup + cross-source agreement vs BA")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum()),
        ("ILCC (3C/Class1/2/3 Brewer + Brew Pub, deduped, active)", df["liquor_count"].sum()),
        ("Brewers Association (2025)", BA_IL_TOTAL_2025),
    ]:
        capture = val / BA_IL_TOTAL_2025 * 100
        print(f"{label:62s} {val:5d}   ({capture:5.1f}% of BA total)")

    print("\nTop 15 counties, all four sources side by side:")
    cmp = df.sort_values("liquor_count", ascending=False).head(15)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count"]].to_string(index=False))


if __name__ == "__main__":
    main()
