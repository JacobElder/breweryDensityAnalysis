"""Fetch OSM Overpass brewery/pub data for all 50 states + DC, sequentially
(the free Overpass instance rate-limits; osm.fetch() already retries on 504/429).
"""

from __future__ import annotations

from breweries.sources import osm
from breweries.state_fips import STATE_FIPS_ALL

STATE_TO_ISO = dict(STATE_FIPS_ALL)  # osm.py builds "US-{code}" ISO3166-2 areas; DC works as "US-DC"


def main() -> None:
    for i, state_abbr in enumerate(sorted(STATE_TO_ISO)):
        try:
            df = osm.load_state(state_abbr)
            print(f"[{i+1}/51] {state_abbr}: {len(df)} records")
        except Exception as e:
            print(f"[{i+1}/51] {state_abbr}: ERROR {e}")


if __name__ == "__main__":
    main()
