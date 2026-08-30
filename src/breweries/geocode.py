"""Assign brewery point records to place, county, and CBSA via TIGER/Line spatial joins.

No paid geocoding APIs. Uses the lat/lon already present in OBDB and OSM records,
falling back to the free Census Geocoder for records with a street address but no
coordinates (see census_geocoder.py), then joins all points against Census
TIGER/Line polygons (src/breweries/sources/tiger.py) with geopandas. CBSA is
derived from the county's CBSAFP attribute (the TIGER county layer already
carries this), not a separate spatial join, since CBSAs are exact aggregates
of counties.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

from breweries.census_geocoder import geocode_addresses
from breweries.manifest import log_filter
from breweries.sources import tiger
from breweries.state_fips import STATE_FIPS_ALL


def fill_missing_coords(
    df: pd.DataFrame, id_col: str, lat_col: str, lon_col: str,
    street_col: str, city_col: str, state_col: str, zip_col: str, source_label: str,
) -> pd.DataFrame:
    """Fall back to the Census Geocoder for rows with an address but no lat/lon."""
    df = df.copy()
    n0 = len(df)
    missing = df[df[lat_col].isna() & df[street_col].notna()]

    if len(missing) == 0:
        return df

    matched = geocode_addresses(missing, id_col, street_col, city_col, state_col, zip_col)
    matched = matched.set_index(id_col)
    df = df.set_index(id_col)
    df.loc[matched.index, lat_col] = df.loc[matched.index, lat_col].fillna(matched["geocoded_lat"])
    df.loc[matched.index, lon_col] = df.loc[matched.index, lon_col].fillna(matched["geocoded_lon"])
    df = df.reset_index()

    n_recovered = (matched["match_indicator"] == "Match").sum()
    log_filter(
        source_label, "Census Geocoder fallback for missing lat/lon",
        n0, n0,
        notes=f"attempted={len(missing)} recovered={int(n_recovered)} "
              f"still_missing={df[lat_col].isna().sum()}",
    )
    return df


def assign_geographies(
    df: pd.DataFrame,
    lat_col: str,
    lon_col: str,
    state_abbr: str,
    source_label: str,
) -> gpd.GeoDataFrame:
    """Spatial-join a brewery DataFrame to county, place, and (derived) CBSA.

    Rows with missing/invalid coordinates, or coordinates that fall outside every
    county polygon, are kept in the output with null geography columns and logged
    — never silently dropped.
    """
    n0 = len(df)
    has_coords = df[lat_col].notna() & df[lon_col].notna()
    df_coord = df[has_coords].copy()
    log_filter(source_label, "has valid lat/lon", n0, len(df_coord))

    points = gpd.GeoDataFrame(
        df_coord,
        geometry=gpd.points_from_xy(df_coord[lon_col], df_coord[lat_col]),
        crs="EPSG:4326",
    )

    state_fips = STATE_FIPS_ALL[state_abbr]
    counties = tiger.load_counties(state_fips)[["GEOID", "NAME", "CBSAFP", "geometry"]].rename(
        columns={"GEOID": "county_geoid", "NAME": "county_name", "CBSAFP": "cbsa_geoid"}
    )
    joined = gpd.sjoin(points, counties, how="left", predicate="within").drop(columns="index_right")

    places = tiger.load_place(state_abbr)[["GEOID", "NAME", "geometry"]].rename(
        columns={"GEOID": "place_geoid", "NAME": "place_name"}
    )
    joined = gpd.sjoin(joined, places, how="left", predicate="within").drop(columns="index_right")

    n_matched_county = joined["county_geoid"].notna().sum()
    log_filter(
        source_label,
        "matched to a county polygon",
        len(joined),
        int(n_matched_county),
        notes=f"unmatched rows kept with null county_geoid; match_rate={n_matched_county / len(joined):.3%}",
    )

    dropped = df[~has_coords].copy()
    for col in ["county_geoid", "county_name", "cbsa_geoid", "place_geoid", "place_name"]:
        dropped[col] = pd.NA
    result = pd.concat([joined.drop(columns="geometry"), dropped], ignore_index=True)

    return result
