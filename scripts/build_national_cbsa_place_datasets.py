"""Build CBSA- and place-level national analysis datasets (county already built in
build_national_county_dataset.py), for the unit-choice sensitivity comparison.
"""

from __future__ import annotations

import glob

import pandas as pd

from breweries.shrinkage import shrink_rates
from breweries.sources.acs import AGE_VARS


def build_cbsa() -> pd.DataFrame:
    geocoded = pd.read_csv("data/processed/obdb_us_geocoded.csv", dtype={"cbsa_geoid": str})
    geocoded = geocoded.dropna(subset=["cbsa_geoid"])
    geocoded["cbsa_geoid"] = geocoded["cbsa_geoid"].str.split(".").str[0].str.zfill(5)
    counts = geocoded.groupby("cbsa_geoid").size().rename("obdb_count")

    acs_cbsa = pd.read_csv(sorted(glob.glob("data/raw/acs/US_cbsa_*.csv"))[-1])
    acs_cbsa.columns = [c.strip() for c in acs_cbsa.columns]
    cbsa_col = [c for c in acs_cbsa.columns if "statistical area" in c.lower()][0]
    acs_cbsa["cbsa_geoid"] = acs_cbsa[cbsa_col].astype(str).str.zfill(5)
    for col in ["B01001_001E"] + AGE_VARS:
        acs_cbsa[col] = pd.to_numeric(acs_cbsa[col], errors="coerce")
    acs_cbsa["total_population"] = acs_cbsa["B01001_001E"]
    acs_cbsa["adults_21plus"] = acs_cbsa[AGE_VARS].sum(axis=1)
    acs_cbsa["cbsa_name"] = acs_cbsa["NAME"]

    df = acs_cbsa[["cbsa_geoid", "cbsa_name", "total_population", "adults_21plus"]].merge(
        counts, on="cbsa_geoid", how="left")
    df["obdb_count"] = df["obdb_count"].fillna(0).astype(int)
    df = df[df["adults_21plus"] > 0]
    df["obdb_rate_per_100k_21plus"] = df["obdb_count"] / df["adults_21plus"] * 100_000

    df = shrink_rates(df, "obdb_count", "adults_21plus")
    df.to_csv("data/processed/us_cbsa_analysis.csv", index=False)
    return df


def build_place() -> pd.DataFrame:
    geocoded = pd.read_csv("data/processed/obdb_us_geocoded.csv", dtype={"place_geoid": str})
    geocoded = geocoded.dropna(subset=["place_geoid"])
    geocoded["place_geoid"] = geocoded["place_geoid"].str.split(".").str[0].str.zfill(7)
    counts = geocoded.groupby("place_geoid").size().rename("obdb_count")

    from breweries.sources import acs
    acs_place = acs.load_national("place")
    acs_place["place_geoid"] = acs_place["state"].astype(str).str.zfill(2) + acs_place["place"].astype(str).str.zfill(5)
    acs_place["place_name"] = acs_place["NAME"]

    df = acs_place[["place_geoid", "place_name", "total_population", "adults_21plus"]].merge(
        counts, on="place_geoid", how="left")
    df["obdb_count"] = df["obdb_count"].fillna(0).astype(int)
    df = df[df["adults_21plus"] > 0]
    df["obdb_rate_per_100k_21plus"] = df["obdb_count"] / df["adults_21plus"] * 100_000

    df = shrink_rates(df, "obdb_count", "adults_21plus")
    df.to_csv("data/processed/us_place_analysis.csv", index=False)
    return df


def main() -> None:
    print("Building CBSA-level dataset...")
    cbsa = build_cbsa()
    print(f"  {len(cbsa)} CBSAs, {cbsa['obdb_count'].sum()} breweries assigned")

    print("Building place-level dataset...")
    place = build_place()
    print(f"  {len(place)} places, {place['obdb_count'].sum()} breweries assigned")

    print("\nTop 15 CBSAs by shrunken rate (pop >= 50k):")
    top_cbsa = cbsa[cbsa["adults_21plus"] >= 50_000].sort_values("eb_posterior_rate_per_100k", ascending=False)
    print(top_cbsa[["cbsa_name", "obdb_count", "adults_21plus", "eb_posterior_rate_per_100k"]].head(15).to_string(index=False))

    print("\nTop 15 places by shrunken rate (pop >= 50k):")
    top_place = place[place["adults_21plus"] >= 50_000].sort_values("eb_posterior_rate_per_100k", ascending=False)
    print(top_place[["place_name", "obdb_count", "adults_21plus", "eb_posterior_rate_per_100k"]].head(15).to_string(index=False))


if __name__ == "__main__":
    main()
