"""Assemble the Florida county-level analysis dataset and run validation checkpoints 1-3.

Calibration state (following NC, MI, CO, OR, WA, TX, GA, WI, PA, IL, CA, NY, VA).
Liquor-registry source is breweries.sources.fl_dbpr -- DBPR/ABT's own weekly
statewide "Alcoholic Beverage Manufacturers/Distributors" (profession 4005)
public-records export, filtered to Series == "CMB" (Manufacturer of Malt
Beverages) (see that module's docstring for the full inclusion-rule writeup,
including why CMBP -- Florida's brewpub-adjacent license class -- yields zero
rows and does not appear to represent a missed population).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, fl_dbpr, obdb, osm

pd.set_option("display.width", 160)

# Checked https://www.brewersassociation.org/statistics-and-data/state-craft-beer-stats/
# on 2026-08-31 (single state-total lookup, no directory scrape): Florida has 379
# craft breweries, ranked #10 nationally.
BA_FL_TOTAL_2025 = 379  # checked 2026-08-31, single state-total lookup


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("Florida")
    df = obdb.apply_inclusion_rule(df, "obdb_fl")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_fl")
    geo = assign_geographies(df, "latitude", "longitude", "FL", "obdb_fl")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("FL")
    geo = assign_geographies(df, "lat", "lon", "FL", "osm_fl")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("FL")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("FL", "county")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "total_population", "adults_21plus"]]


def build_liquor_county_counts() -> pd.DataFrame:
    # fl_dbpr already carries DBPR's own county column -- no geocoding/spatial
    # join needed (same precedent as il_liquor.py / pa_liquor.py / mi_lara.py),
    # see module docstring.
    df = fl_dbpr.county_counts()
    return df.rename(columns={"fl_dbpr_count": "liquor_count"})


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

    out_path = Path("data/processed/fl_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} counties)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (Miami-Dade/Broward/Hillsborough/Orange should dominate")
    print("absolute counts, but may rank lower per-capita due to population dilution -- same")
    print("pattern as other large-metro counties already seen in this project)")
    print("=" * 70)
    top = df[df["adults_21plus"] >= 50_000].sort_values("obdb_rate_per_100k_21plus", ascending=False)
    print(top[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].head(15).to_string(index=False))
    print("\nRaw counts for Miami-Dade County specifically:")
    print(df[df["county_name"] == "Miami-Dade"][
        ["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count", "adults_21plus"]
    ].to_string(index=False))
    top_reset = top.reset_index(drop=True)
    if "Miami-Dade" in top_reset["county_name"].values:
        rank = int(top_reset.index[top_reset["county_name"] == "Miami-Dade"][0]) + 1
        print(f"\nMiami-Dade County per-capita rank among counties with 50k+ adults 21+: {rank} of {len(top_reset)}")

    print("\n" + "=" * 70)
    print("CHECKPOINT 2/3: State rollup + cross-source agreement vs BA")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum()),
        ("DBPR/ABT (Series CMB -- Manufacturer of Malt Beverages, active)", df["liquor_count"].sum()),
        ("Brewers Association (2025)", BA_FL_TOTAL_2025),
    ]:
        capture = val / BA_FL_TOTAL_2025 * 100
        print(f"{label:62s} {val:5d}   ({capture:5.1f}% of BA total)")

    print("\nTop 15 counties, all four sources side by side:")
    cmp = df.sort_values("liquor_count", ascending=False).head(15)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count"]].to_string(index=False))


if __name__ == "__main__":
    main()
