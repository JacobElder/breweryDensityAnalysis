"""Oregon Liquor and Cannabis Commission (OLCC) licenses, via the state's Socrata open-data API.

Source: https://data.oregon.gov/resource/srxe-qkm2.json (dataset "OLCC Liquor
Business Licenses & Endorsements", id srxe-qkm2). Record-level, includes a
"county" field directly (no geocoding needed for county-level rollups), and
critically distinguishes primary licenses from "ADDITIONAL LOCATION" licenses
— OLCC's own answer to the satellite-taproom question the project handoff
flags as a judgment call. PRIMARY_TYPES is the project's default (one license
per independently-licensed brewery); ADDITIONAL_LOCATION_TYPES are counted
separately for the satellite-taproom sensitivity check.
"""

from __future__ import annotations

import glob
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

RAW_DIR = Path("data/raw/or_olcc")
SODA_URL = "https://data.oregon.gov/resource/srxe-qkm2.json"

PRIMARY_TYPES = ["BREWERY PUBLIC HOUSE", "BREWERY - CONSUMPTION", "BREWERY - NON-CONSUMPTION"]
ADDITIONAL_LOCATION_TYPES = [
    "BREWERY PUBLIC HOUSE ADDITIONAL LOCATION",
    "BREWERY - CONSUMPTION ADDITIONAL LOCATION",
]
ALL_BREWERY_TYPES = PRIMARY_TYPES + ADDITIONAL_LOCATION_TYPES


def fetch(force: bool = False) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "or_breweries_*.json")))
    if existing and not force:
        return Path(existing[-1])

    where_clause = (
        "license_expired='No' AND license_type in ("
        + ",".join(f"'{t}'" for t in ALL_BREWERY_TYPES) + ")"
    )
    resp = requests.get(SODA_URL, params={"$where": where_clause, "$limit": 1000}, timeout=60)
    resp.raise_for_status()
    records = resp.json()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"or_breweries_{ts}.json"
    dest.write_text(json.dumps(records))

    log_fetch(source="or_olcc", url=SODA_URL, dest_path=str(dest), row_count=len(records),
              notes=f"license_types={ALL_BREWERY_TYPES}, active only")
    return dest


def load(include_additional_locations: bool = False) -> pd.DataFrame:
    """Load active OR brewery licenses. Default excludes satellite/additional-location
    licenses (one row per independently-licensed brewery); pass
    include_additional_locations=True for the satellite-taproom sensitivity check.
    """
    path = fetch()
    records = json.loads(path.read_text())
    df = pd.DataFrame(records)
    n0 = len(df)

    types = ALL_BREWERY_TYPES if include_additional_locations else PRIMARY_TYPES
    filtered = df[df["license_type"].isin(types)]
    log_filter("or_olcc", f"include_additional_locations={include_additional_locations}", n0, len(filtered))

    return filtered.reset_index(drop=True)


def county_counts(include_additional_locations: bool = False) -> pd.DataFrame:
    df = load(include_additional_locations)
    counts = df.groupby("county").size().rename("olcc_count").reset_index()
    counts.columns = ["county_name", "olcc_count"]
    return counts
