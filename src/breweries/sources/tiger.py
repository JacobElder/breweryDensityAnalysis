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

# Cartographic Boundary files: the SAME counties, generalized and clipped to
# the shoreline. TIGER/Line carries *legal* boundaries, which extend county
# polygons out over open water wherever a county's jurisdiction does -- so a
# TIGER-based choropleth fills in the Great Lakes, Chesapeake Bay, Long Island
# Sound and the Gulf with solid county colour. 248 counties are more than 25%
# water by area in TIGER and 107 are more than half; Keweenaw County MI is 91%
# water, Leelanau County MI 86%.
#
# That is not merely cosmetic here. The Great Lakes counties with the largest
# water areas are also small-population counties carrying the noisiest rate
# estimates, so the bug paints tens of thousands of square kilometres of open
# lake in the colour of the least reliable numbers in the dataset. It was the
# single most-remarked-on defect when the map was published.
#
# CB files are for DISPLAY ONLY. Every spatial join (geocoding a brewery to a
# county, building the Queen contiguity graph) must keep using the TIGER/Line
# geometry, which is the authoritative, untruncated boundary -- a point that
# falls in a county's water area still belongs to that county.
CB_YEAR = 2024
CB_BASE_URL = f"https://www2.census.gov/geo/tiger/GENZ{CB_YEAR}/shp"
# 500k = 1:500,000, the most detailed of the three published resolutions.
CB_RESOLUTION = "500k"


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
    # Must exclude the cartographic-boundary cache: it is named
    # "us_county_cb_*.parquet", which the bare "us_county_*" glob matches AND
    # sorts last (the timestamped TIGER name starts with a digit), so the
    # legal-boundary loader would silently start returning shoreline-clipped
    # geometry -- quietly changing every spatial join and contiguity graph in
    # the project.
    existing = sorted(p for p in glob.glob(str(RAW_DIR / "us_county_*.parquet"))
                      if "_cb_" not in Path(p).name)
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


def fetch_cb_counties(force: bool = False) -> Path:
    """Cartographic Boundary counties -- shoreline-clipped, for display."""
    existing = sorted(glob.glob(str(RAW_DIR / "us_county_cb_*.parquet")))
    if existing and not force:
        return Path(existing[-1])
    url = f"{CB_BASE_URL}/cb_{CB_YEAR}_us_county_{CB_RESOLUTION}.zip"
    dest = RAW_DIR / f"us_county_cb_{CB_YEAR}_{CB_RESOLUTION}.parquet"
    return _download_and_convert(
        url, dest, f"national county polygons, cartographic boundary {CB_RESOLUTION}")


def load_counties(state_fips: str | None = None) -> gpd.GeoDataFrame:
    """TIGER/Line counties: legal boundaries, including water. Use for spatial
    joins and contiguity graphs -- NOT for choropleths (see `load_cb_counties`).
    """
    gdf = gpd.read_parquet(fetch_counties())
    if state_fips:
        gdf = gdf[gdf["STATEFP"] == state_fips]
    return gdf.to_crs(epsg=4326)


def load_cb_counties(state_fips: str | None = None) -> gpd.GeoDataFrame:
    """Cartographic Boundary counties: clipped to the shoreline, so the Great
    Lakes, Chesapeake Bay and coastal water read as water instead of as
    coloured county area. Use for any map a human looks at.

    Same GEOID/STATEFP/NAMELSAD keys as `load_counties`, so it is a drop-in
    swap for display purposes.
    """
    gdf = gpd.read_parquet(fetch_cb_counties())
    if state_fips:
        gdf = gdf[gdf["STATEFP"] == state_fips]
    return gdf.to_crs(epsg=4326)


def load_cbsas() -> gpd.GeoDataFrame:
    return gpd.read_parquet(fetch_cbsas()).to_crs(epsg=4326)


def load_place(state_abbr: str) -> gpd.GeoDataFrame:
    return gpd.read_parquet(fetch_place(state_abbr)).to_crs(epsg=4326)
