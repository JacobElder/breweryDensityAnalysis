"""TIGER/Line county, CBSA, and place polygons, cached as GeoParquet.

Census only serves these as zipped shapefiles; this module downloads the zip
into memory, converts it, and caches only the GeoParquet result — never a
permanent .zip — compressed with brotli. Brotli was chosen after checking:
pyarrow's default (snappy) compression left these files *larger* than the
source zip (WKB polygon geometry doesn't snappy-compress well), while brotli
brings the national county file from an 80MB zip to ~57MB, at the cost of
slower writes (~15s for that file) — acceptable since this runs once per fetch.
"""

from __future__ import annotations

import glob
import io
from pathlib import Path

import geopandas as gpd
import requests

from breweries.manifest import log_fetch
from breweries.state_fips import STATE_FIPS_ALL

RAW_DIR = Path("data/raw/tiger")
TIGER_YEAR = 2025
BASE_URL = f"https://www2.census.gov/geo/tiger/TIGER{TIGER_YEAR}"
COMPRESSION = "brotli"


def _download_and_convert(url: str, dest: Path, source_label: str) -> Path:
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()

    gdf = gpd.read_file(io.BytesIO(resp.content))
    dest.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(dest, compression=COMPRESSION)

    log_fetch(source="tiger", url=url, dest_path=str(dest), row_count=len(gdf),
              notes=f"{source_label}, TIGER{TIGER_YEAR}, cached as GeoParquet ({COMPRESSION})")
    return dest


def fetch_counties(force: bool = False) -> Path:
    existing = sorted(glob.glob(str(RAW_DIR / "us_county_*.parquet")))
    if existing and not force:
        return Path(existing[-1])
    url = f"{BASE_URL}/COUNTY/tl_{TIGER_YEAR}_us_county.zip"
    dest = RAW_DIR / f"us_county_{TIGER_YEAR}.parquet"
    return _download_and_convert(url, dest, "national county polygons")


def fetch_cbsas(force: bool = False) -> Path:
    existing = sorted(glob.glob(str(RAW_DIR / "us_cbsa_*.parquet")))
    if existing and not force:
        return Path(existing[-1])
    url = f"{BASE_URL}/CBSA/tl_{TIGER_YEAR}_us_cbsa.zip"
    dest = RAW_DIR / f"us_cbsa_{TIGER_YEAR}.parquet"
    return _download_and_convert(url, dest, "national CBSA polygons")


def fetch_place(state_abbr: str, force: bool = False) -> Path:
    existing = sorted(glob.glob(str(RAW_DIR / f"{state_abbr.lower()}_place_*.parquet")))
    if existing and not force:
        return Path(existing[-1])
    fips = STATE_FIPS_ALL[state_abbr]
    url = f"{BASE_URL}/PLACE/tl_{TIGER_YEAR}_{fips}_place.zip"
    dest = RAW_DIR / f"{state_abbr.lower()}_place_{TIGER_YEAR}.parquet"
    return _download_and_convert(url, dest, f"{state_abbr} place polygons")


def fetch_all_places(force: bool = False) -> None:
    """Fetch all 50 states + DC place files (sequential; each is a single request)."""
    for state_abbr in sorted(STATE_FIPS_ALL):
        fetch_place(state_abbr, force=force)


def load_counties(state_fips: str | None = None) -> gpd.GeoDataFrame:
    gdf = gpd.read_parquet(fetch_counties())
    if state_fips:
        gdf = gdf[gdf["STATEFP"] == state_fips]
    return gdf.to_crs(epsg=4326)


def load_cbsas() -> gpd.GeoDataFrame:
    return gpd.read_parquet(fetch_cbsas()).to_crs(epsg=4326)


def load_place(state_abbr: str) -> gpd.GeoDataFrame:
    return gpd.read_parquet(fetch_place(state_abbr)).to_crs(epsg=4326)
