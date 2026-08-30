"""Colorado Liquor Enforcement Division licenses, via the state's Socrata open-data API.

Source: https://data.colorado.gov/resource/ier5-5ms2.json (dataset "Liquor
Licenses in Colorado", id ier5-5ms2). A genuine bulk open-data API — record
level, with lat/lon already attached, no geocoding needed. Brewery-relevant
license types: "Manufacturer (brewery)" (production breweries), "Brew Pub
(city)" and "Brew Pub (county)" (brewpubs licensed at the city vs. county
jurisdiction level — mutually exclusive by location, not double-licensing).
"""

from __future__ import annotations

import glob
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

RAW_DIR = Path("data/raw/co_liquor")
SODA_URL = "https://data.colorado.gov/resource/ier5-5ms2.json"
BREWERY_LICENSE_TYPES = ["Manufacturer (brewery)", "Brew Pub (city)", "Brew Pub (county)"]


def fetch(force: bool = False) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "co_breweries_*.json")))
    if existing and not force:
        return Path(existing[-1])

    where_clause = "license_type in (" + ",".join(f"'{t}'" for t in BREWERY_LICENSE_TYPES) + ")"
    resp = requests.get(SODA_URL, params={"$where": where_clause, "$limit": 2000}, timeout=60)
    resp.raise_for_status()
    records = resp.json()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"co_breweries_{ts}.json"
    dest.write_text(json.dumps(records))

    log_fetch(source="co_liquor", url=SODA_URL, dest_path=str(dest), row_count=len(records),
              notes=f"license_types={BREWERY_LICENSE_TYPES}")
    return dest


def load() -> pd.DataFrame:
    path = fetch()
    records = json.loads(path.read_text())
    n0 = len(records)

    rows = []
    for r in records:
        loc = r.get("location") or {}
        rows.append({
            "licensee_name": r.get("licensee_name"),
            "doing_business_as": r.get("doing_business_as"),
            "license_number": r.get("license_number"),
            "license_type": r.get("license_type"),
            "street_address": r.get("street_address"),
            "city": r.get("city"),
            "zip": r.get("zip"),
            "lat": float(loc["latitude"]) if loc.get("latitude") else None,
            "lon": float(loc["longitude"]) if loc.get("longitude") else None,
        })
    df = pd.DataFrame(rows)

    has_coords = df["lat"].notna() & df["lon"].notna()
    log_filter("co_liquor", "has valid lat/lon", n0, int(has_coords.sum()))

    return df
