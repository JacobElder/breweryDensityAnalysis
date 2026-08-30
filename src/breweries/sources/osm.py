"""OpenStreetMap source via Overpass API: craft=brewery, microbrewery=yes, amenity=pub+microbrewery.

Free, no auth. Coverage is uneven but its errors are uncorrelated with OBDB's, which is
what makes it useful as an independent third signal.
"""

from __future__ import annotations

import glob
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
RAW_DIR = Path("data/raw/osm")

# US state postal codes used as ISO3166-2 area filters, e.g. "US-NC".
QUERY_TEMPLATE = """
[out:json][timeout:120];
area["ISO3166-2"="US-{state_code}"]["admin_level"="4"]->.st;
(
  node["craft"="brewery"](area.st);
  way["craft"="brewery"](area.st);
  node["microbrewery"="yes"](area.st);
  way["microbrewery"="yes"](area.st);
  node["amenity"="pub"]["microbrewery"="yes"](area.st);
);
out center tags;
"""


def fetch(state_code: str, force: bool = False) -> Path:
    """Query Overpass for a state, or reuse the existing cached copy."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / f"{state_code}_*.json")))
    if existing and not force:
        return Path(existing[-1])

    query = QUERY_TEMPLATE.format(state_code=state_code)
    headers = {"User-Agent": "brewery-density-analysis/0.1 (research project)"}

    resp = None
    for attempt in range(4):
        resp = requests.post(OVERPASS_URL, data={"data": query}, headers=headers, timeout=180)
        if resp.status_code in (504, 429):
            time.sleep(10 * (attempt + 1))
            continue
        break
    resp.raise_for_status()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"{state_code}_{ts}.json"
    dest.write_bytes(resp.content)

    n_elements = len(resp.json().get("elements", []))
    log_fetch(source="osm_overpass", url=OVERPASS_URL, dest_path=str(dest), row_count=n_elements,
              notes=f"state_code={state_code}")
    return dest


def load_state(state_code: str) -> pd.DataFrame:
    """Load cached Overpass JSON for a state into a flat DataFrame, logging row counts."""
    path = fetch(state_code)
    data = json.loads(path.read_text())
    elements = data["elements"]
    n0 = len(elements)

    rows = []
    for el in elements:
        tags = el.get("tags", {})
        if el["type"] == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            center = el.get("center", {})
            lat, lon = center.get("lat"), center.get("lon")
        rows.append({
            "osm_id": el["id"],
            "osm_type": el["type"],
            "name": tags.get("name"),
            "craft": tags.get("craft"),
            "microbrewery": tags.get("microbrewery"),
            "amenity": tags.get("amenity"),
            "lat": lat,
            "lon": lon,
        })
    df = pd.DataFrame(rows)

    # Drop elements with no name and no coordinates — not usable as brewery records.
    df_clean = df[df["name"].notna() & df["lat"].notna() & df["lon"].notna()]
    log_filter("osm_overpass", "drop unnamed / uncoordinated elements", n0, len(df_clean))

    return df_clean.reset_index(drop=True)
