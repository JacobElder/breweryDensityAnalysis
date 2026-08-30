"""Geocode all US OBDB breweries (post inclusion-filter) to county/place/CBSA.

Loops per state so each state's TIGER place file (small) is read once; the
national county file (large, ~84MB) is read once and reused across states
rather than reloaded 51 times.
"""

from __future__ import annotations

import time

import geopandas as gpd
import pandas as pd

from breweries.census_geocoder import geocode_addresses
from breweries.manifest import log_filter
from breweries.sources import obdb, tiger
from breweries.state_fips import STATE_FIPS_ALL, STATE_NAME_TO_ABBR

pd.set_option("display.width", 160)


def load_national_counties() -> gpd.GeoDataFrame:
    gdf = tiger.load_counties()
    return gdf[["STATEFP", "GEOID", "NAME", "CBSAFP", "geometry"]].rename(
        columns={"GEOID": "county_geoid", "NAME": "county_name", "CBSAFP": "cbsa_geoid"}
    )


def geocode_state(df_state: pd.DataFrame, state_abbr: str, counties_national: gpd.GeoDataFrame) -> pd.DataFrame:
    fips = STATE_FIPS_ALL[state_abbr]
    n0 = len(df_state)

    has_coords = df_state["latitude"].notna() & df_state["longitude"].notna()
    df_coord = df_state[has_coords].copy()

    points = gpd.GeoDataFrame(
        df_coord,
        geometry=gpd.points_from_xy(df_coord["longitude"], df_coord["latitude"]),
        crs="EPSG:4326",
    )
    counties = counties_national[counties_national["STATEFP"] == fips]
    joined = gpd.sjoin(points, counties.drop(columns="STATEFP"), how="left", predicate="within").drop(columns="index_right")

    places = tiger.load_place(state_abbr)
    places = places[["GEOID", "NAME", "geometry"]].rename(columns={"GEOID": "place_geoid", "NAME": "place_name"})
    joined = gpd.sjoin(joined, places, how="left", predicate="within").drop(columns="index_right")

    dropped = df_state[~has_coords].copy()
    for col in ["county_geoid", "county_name", "cbsa_geoid", "place_geoid", "place_name"]:
        dropped[col] = pd.NA
    result = pd.concat([joined.drop(columns="geometry"), dropped], ignore_index=True)

    n_matched = result["county_geoid"].notna().sum()
    log_filter(f"obdb_us_{state_abbr}", "matched to a county polygon", n0, int(n_matched))
    return result


def main() -> None:
    df = obdb.load_us()
    df = obdb.apply_inclusion_rule(df, "US")
    df["state_abbr"] = df["state_province"].map(STATE_NAME_TO_ABBR)

    unmapped = df[df["state_abbr"].isna()]
    if len(unmapped):
        print(f"WARNING: {len(unmapped)} rows with unmapped state_province: "
              f"{unmapped['state_province'].unique()}")

    # Fill missing coordinates via Census Geocoder for rows with a street address
    missing = df[df["latitude"].isna() & df["address_1"].notna()].copy()
    print(f"Geocoding {len(missing)} US records missing lat/lon (this may take a few minutes)...")
    if len(missing):
        matched = geocode_addresses(missing, "id", "address_1", "city", "state_province", "postal_code")
        matched = matched.set_index("id")
        df = df.set_index("id")
        df.loc[matched.index, "latitude"] = df.loc[matched.index, "latitude"].fillna(matched["geocoded_lat"])
        df.loc[matched.index, "longitude"] = df.loc[matched.index, "longitude"].fillna(matched["geocoded_lon"])
        df = df.reset_index()
        n_recovered = (matched["match_indicator"] == "Match").sum()
        log_filter("obdb_us", "Census Geocoder fallback for missing lat/lon", len(df), len(df),
                   notes=f"attempted={len(missing)} recovered={int(n_recovered)}")

    print("Loading national county polygons...")
    counties_national = load_national_counties()

    results = []
    t0 = time.time()
    for i, state_abbr in enumerate(sorted(df["state_abbr"].dropna().unique())):
        df_state = df[df["state_abbr"] == state_abbr]
        geo = geocode_state(df_state, state_abbr, counties_national)
        results.append(geo)
        print(f"  [{i+1}/51] {state_abbr}: {len(df_state)} records, "
              f"{geo['county_geoid'].notna().sum()} matched ({time.time()-t0:.0f}s elapsed)")

    national = pd.concat(results, ignore_index=True)
    national.to_parquet("data/processed/obdb_us_geocoded.parquet", index=False)

    match_rate = national["county_geoid"].notna().mean()
    print(f"\nTotal: {len(national)} records, {national['county_geoid'].notna().sum()} matched "
          f"({match_rate:.2%})")
    print("Wrote data/processed/obdb_us_geocoded.parquet")


if __name__ == "__main__":
    main()
