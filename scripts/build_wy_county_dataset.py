"""Assemble the Wyoming county-level analysis dataset and run validation checkpoints 1-3.

Calibration state (following NC, MI, CO, OR, WA, TX, GA, WI, PA, IL, CA, NY, VA, KY, FL,
CT, WV). Liquor-registry source is breweries.sources.wy_liquor -- the WY Department of
Revenue Liquor Division's "Wyoming Malt Beverage Wholesaler List" PDF, linked from
liquor365.wyo.gov's own homepage (see that module's docstring for the full
inclusion-rule writeup: Wyoming licenses brewers as their own wholesale distributor
under W.S. 12-4-201, so the 58-row combined wholesaler/distributor list required
hand-classifying every row into 28 physical breweries vs. 30 third-party
distributors/non-beer producers).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, obdb, osm, wy_liquor

pd.set_option("display.width", 160)

# Checked https://www.brewersassociation.org/statistics-and-data/state-craft-beer-stats/
# on 2026-08-31 (single state-total lookup, no directory scrape): Wyoming has 49 craft
# breweries (ranks 40th).
BA_WY_TOTAL_2025 = 49  # checked 2026-08-31, single state-total lookup


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("Wyoming")
    df = obdb.apply_inclusion_rule(df, "obdb_wy")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_wy")
    geo = assign_geographies(df, "latitude", "longitude", "WY", "obdb_wy")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("WY")
    geo = assign_geographies(df, "lat", "lon", "WY", "osm_wy")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("WY")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("WY", "county")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "total_population", "adults_21plus"]]


def build_liquor_county_counts() -> pd.DataFrame:
    # wy_liquor already carries the Wholesaler List's own County column -- no
    # geocoding/spatial join needed, same precedent as il_liquor.py/wv_abca.py.
    df = wy_liquor.county_counts()
    return df.rename(columns={"wy_liquor_count": "liquor_count"})


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

    out_path = Path("data/processed/wy_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} counties)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity")
    print("=" * 70)
    top = df[df["adults_21plus"] >= 5_000].sort_values("obdb_rate_per_100k_21plus", ascending=False)
    print(top[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].head(15).to_string(index=False))

    print("\n" + "=" * 70)
    print("CHECKPOINT 2/3: State rollup + cross-source agreement vs BA")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum()),
        ("WY Liquor Div (Malt Beverage Wholesaler List, hand-classified)", df["liquor_count"].sum()),
        ("Brewers Association (2025)", BA_WY_TOTAL_2025),
    ]:
        capture = val / BA_WY_TOTAL_2025 * 100
        print(f"{label:62s} {val:5d}   ({capture:5.1f}% of BA total)")

    obdb_total = int(df["obdb_count"].sum())
    liquor_total = int(df["liquor_count"].sum())
    print(f"\nRaw capture rate (obdb_count / liquor_count): {obdb_total} / {liquor_total} = "
          f"{obdb_total / liquor_total * 100:.1f}%")

    print("\nTop 15 counties, all four sources side by side:")
    cmp = df.sort_values("liquor_count", ascending=False).head(15)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count"]].to_string(index=False))


if __name__ == "__main__":
    main()
