"""Assemble the Wisconsin county-level analysis dataset and run validation checkpoints 1-3.

Calibration state (following NC, MI, CO, OR). Liquor-registry source is
breweries.sources.wi_dor — the WI DOR/DAB statewide Brewer's/Brewpub Permit
list (see that module's docstring for why this is the right list, as opposed
to the ~15,000 municipal retail licenses that are not centrally published).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, obdb, osm, wi_dor

pd.set_option("display.width", 160)

BA_WI_TOTAL_2025 = 247  # checked 2026-08-30, single state-total lookup


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("Wisconsin")
    df = obdb.apply_inclusion_rule(df, "obdb_wi")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_wi")
    geo = assign_geographies(df, "latitude", "longitude", "WI", "obdb_wi")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("WI")
    geo = assign_geographies(df, "lat", "lon", "WI", "osm_wi")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("WI")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("WI", "county")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "total_population", "adults_21plus"]]


def build_liquor_county_counts() -> pd.DataFrame:
    df = wi_dor.load()
    df = fill_missing_coords(df, "wi_dor_id", "lat", "lon", "street_address", "city",
                              "state", "zip", "wi_dor")
    geo = assign_geographies(df, "lat", "lon", "WI", "wi_dor")
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

    out_path = Path("data/processed/wi_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} counties)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (Milwaukee County and Dane County/Madison should rank prominently)")
    print("=" * 70)
    top = df[df["adults_21plus"] >= 20_000].sort_values("obdb_rate_per_100k_21plus", ascending=False)
    print(top[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].head(10).to_string(index=False))

    print("\nRaw counts, Milwaukee and Dane specifically:")
    focus = df[df["county_name"].isin(["Milwaukee", "Dane"])]
    print(focus[["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count", "adults_21plus"]].to_string(index=False))

    print("\n" + "=" * 70)
    print("CHECKPOINT 2/3: State rollup + cross-source agreement vs BA")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum()),
        ("WI DOR/DAB (Brewery + Brewpub permits)", df["liquor_count"].sum()),
        ("Brewers Association (2025)", BA_WI_TOTAL_2025),
    ]:
        capture = val / BA_WI_TOTAL_2025 * 100
        print(f"{label:62s} {val:5d}   ({capture:5.1f}% of BA total)")

    print("\nTop 10 counties, all four sources side by side:")
    cmp = df.sort_values("liquor_count", ascending=False).head(10)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count"]].to_string(index=False))


if __name__ == "__main__":
    main()
