"""Fetch TIGER/Line PLACE polygons for all 50 states + DC, cached as GeoParquet.

Thin wrapper around breweries.sources.tiger — see that module for the
fetch-and-convert-in-memory implementation (no .zip ever touches disk).
"""

from __future__ import annotations

from breweries.sources import tiger


def main() -> None:
    tiger.fetch_all_places()
    print("Done — see data/raw/tiger/*_place_*.parquet")


if __name__ == "__main__":
    main()
