"""Download TIGER/Line PLACE shapefiles for all 50 states + DC, in parallel.

Skips states that already have a cached file (matches the existing per-state
naming used by NC/MI/CO/OR: data/raw/tiger/{state_lower}_place_*.zip).
"""

from __future__ import annotations

import glob
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

from breweries.manifest import log_fetch
from breweries.state_fips import STATE_FIPS_ALL

RAW_DIR = Path("data/raw/tiger")
URL_TEMPLATE = "https://www2.census.gov/geo/tiger/TIGER2025/PLACE/tl_2025_{fips}_place.zip"


def fetch_one(state_abbr: str, fips: str) -> tuple[str, str]:
    existing = sorted(glob.glob(str(RAW_DIR / f"{state_abbr.lower()}_place_*.zip")))
    if existing:
        return state_abbr, f"cached: {existing[-1]}"

    url = URL_TEMPLATE.format(fips=fips)
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"{state_abbr.lower()}_place_{ts}.zip"
    dest.write_bytes(resp.content)

    log_fetch(source="tiger", url=url, dest_path=str(dest), notes=f"{state_abbr} place polygons, TIGER2025")
    return state_abbr, f"fetched {len(resp.content)} bytes"


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_one, abbr, fips): abbr for abbr, fips in STATE_FIPS_ALL.items()}
        for fut in as_completed(futures):
            abbr = futures[fut]
            try:
                _, msg = fut.result()
                print(f"{abbr}: {msg}")
            except Exception as e:
                print(f"{abbr}: ERROR {e}")


if __name__ == "__main__":
    main()
