"""Assemble the Kentucky county-level analysis dataset and run validation checkpoints 1-3.

Calibration state (following NC, MI, CO, OR, WA, TX, GA, WI, PA, IL, CA, NY, VA).
Liquor-registry source is breweries.sources.ky_abc -- Kentucky ABC's own BELLE
Portal LicenseLookup grid (LicenseType=MIC, "Microbrewery License"), a public,
no-login JSON endpoint that returns the state's full active microbrewery roster
in one request. See that module's docstring for the full inclusion-rule writeup,
including why KY's separate "Brewer's License" (MB) type is excluded (3 non-craft
industrial holders, not breweries) and why no active-only filter needed to be
applied here (the BELLE Portal's LOOKUP tool is active-licensees-only by design).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, ky_abc, obdb, osm

pd.set_option("display.width", 160)

# Checked https://www.brewersassociation.org/statistics-and-data/state-craft-beer-stats/
# on 2026-08-31 (single state-total lookup, no directory scrape): Kentucky has 96
# craft breweries, ranked #32 nationally.
BA_KY_TOTAL_2025 = 96  # checked 2026-08-31, single state-total lookup


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("Kentucky")
    df = obdb.apply_inclusion_rule(df, "obdb_ky")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_ky")
    geo = assign_geographies(df, "latitude", "longitude", "KY", "obdb_ky")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("KY")
    geo = assign_geographies(df, "lat", "lon", "KY", "osm_ky")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("KY")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("KY", "county")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "total_population", "adults_21plus"]]


def build_liquor_county_counts() -> pd.DataFrame:
    # ky_abc already carries KY ABC's own county column -- no geocoding/spatial
    # join needed (same precedent as il_liquor.py / pa_liquor.py / mi_lara.py).
    df = ky_abc.county_counts()
    return df.rename(columns={"ky_abc_count": "liquor_count"})


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

    out_path = Path("data/processed/ky_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} counties)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (Jefferson/Louisville and Fayette/Lexington")
    print("should dominate absolute counts, but may rank lower per-capita due to")
    print("population dilution -- same pattern as other large-metro counties already")
    print("seen in this project)")
    print("=" * 70)
    top = df[df["adults_21plus"] >= 20_000].sort_values("obdb_rate_per_100k_21plus", ascending=False)
    print(top[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].head(15).to_string(index=False))
    print("\nRaw counts for Jefferson and Fayette specifically:")
    print(df[df["county_name"].isin(["Jefferson", "Fayette"])][
        ["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count", "adults_21plus"]
    ].to_string(index=False))

    print("\n" + "=" * 70)
    print("CHECKPOINT 2/3: State rollup + cross-source agreement vs BA")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum()),
        ("KY ABC (Microbrewery License, active)", df["liquor_count"].sum()),
        ("Brewers Association (2025)", BA_KY_TOTAL_2025),
    ]:
        capture = val / BA_KY_TOTAL_2025 * 100
        print(f"{label:62s} {val:5d}   ({capture:5.1f}% of BA total)")

    n_obdb = df["obdb_count"].sum()
    n_liquor = df["liquor_count"].sum()
    print(f"\nRaw OBDB/KY-ABC capture rate: {n_obdb} / {n_liquor} = {n_obdb / n_liquor * 100:.1f}%")

    print("\nTop 15 counties, all four sources side by side:")
    cmp = df.sort_values("liquor_count", ascending=False).head(15)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count"]].to_string(index=False))


if __name__ == "__main__":
    main()
