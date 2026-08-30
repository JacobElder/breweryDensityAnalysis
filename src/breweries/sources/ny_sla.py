"""New York State Liquor Authority (SLA) active licenses, via data.ny.gov's Socrata API.

Source: https://data.ny.gov/resource/9s3h-dpkz.json (dataset "Current Liquor
Authority Active Licenses", id 9s3h-dpkz, ~60k rows, refreshed daily). A genuine
bulk open-data API — record-level, with a GeoJSON `georeference` point and a
`premisescounty` field already attached for nearly every row. This was the
handoff brief's named starting point for New York and it holds up: no scraping
of an interactive search form was needed.

License classes. The SLA's "class" codes map to descriptions; the ones that
correspond to a physical, currently-operating brewing location are:

  0013  Brewer                    standard/large manufacturer brewery license
  0014  Micro-Brewer              production-capped manufacturer brewery license
  0018  Micro-Brewer 3 Year       same as 0014, 3-year renewal term
  0015  Farm Brewer               NY-specific: brewery using NY-grown ingredients
  0416  Restaurant Brewer         brewpub — a restaurant license with on-premise brewing

Per the handoff hint, NY does have both a plain "Brewer" (0013/0014/0018) and a
"Farm Brewer" (0015) manufacturer category, and both are included: a Farm Brewer
license is still a physical brewing facility, just one restricted to NY-grown
ingredients, not a different kind of building. 0416 "Restaurant Brewer" is kept
as the brewpub-adjacent case (mirrors CO's Brew Pub license classes).

Explicitly excluded as separate beverage categories, not breweries: Cider
Producer / Farm Cidery / Farm Meadery, Distiller Class A/A-1/B/B-1/C/D, Winery /
Farm winery / Special (Farm) Winery / MicroFarm Winery, Wholesale Beer/Wine/
Liquor (distributors, not manufacturers), and the generic Restaurant/Grocery/
Liquor Store/Club retail classes.

One class deliberately excluded despite touching real breweries: "CM" /
"Combined Craft Status" (218 rows). Spot-checking confirmed this is an
administrative overlay license, not a standalone location: every CM row checked
(Big Ditch Brewing, Sloop Brewing Co / Hudson Valley Beverage, Newburgh Brewing
Company) already has its own separate 0013/0014/0015 record at the identical
address. Counting CM would double-count those breweries. This mirrors this
project's general rule of preferring physical-location counts over license-row
counts.

That same physical-vs-license-row distinction shows up again within the five
included classes themselves: of 709 raw rows, 498 unique (address, zip) pairs.
196 addresses carry more than one row — 182 are one licensee holding two brewer
classes at one site (e.g. a Farm Brewer + Micro-Brewer license at the same
brewery), and 14 are genuine contract-brewing / co-packing arrangements where
multiple brands are licensed at one physical facility (e.g. Mark Anthony
Brewing / Narragansett / High Falls Operating Co. all list 445 St Paul St,
Rochester — one facility, three brand licensees; Matt Brewing Co. and The
Brooklyn Brewery Corporation both list 811 Edward Street, Utica, for the same
reason). This module deduplicates to one row per (address, zip) so county
counts reflect physical brewing locations, not license counts, consistent with
the project's brewery definition elsewhere (OBDB's brewery_type inclusion rule,
CBP's establishment counts).

Almost every row already carries a georeference point (706 of 709 pre-dedup);
the 3 rows missing coordinates all have a full street address, so they are
routed through breweries.geocode.fill_missing_coords, the same Census Geocoder
fallback OBDB and ga_dor use.
"""

from __future__ import annotations

import glob
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

RAW_DIR = Path("data/raw/ny_sla")
SODA_URL = "https://data.ny.gov/resource/9s3h-dpkz.json"

# class code -> description, restricted to physical brewing-location licenses.
BREWERY_LICENSE_CLASSES = {
    "0013": "Brewer",
    "0014": "Micro-Brewer",
    "0018": "Micro-Brewer 3 Year",
    "0015": "Farm Brewer",
    "0416": "Restaurant Brewer",
}


def fetch(force: bool = False) -> Path:
    """Query the SLA Socrata endpoint for brewery-relevant license classes, or reuse the cache."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "ny_sla_breweries_*.json")))
    if existing and not force:
        return Path(existing[-1])

    where_clause = "class in (" + ",".join(f"'{c}'" for c in BREWERY_LICENSE_CLASSES) + ")"
    resp = requests.get(SODA_URL, params={"$where": where_clause, "$limit": 5000}, timeout=60)
    resp.raise_for_status()
    records = resp.json()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"ny_sla_breweries_{ts}.json"
    dest.write_text(json.dumps(records))

    log_fetch(source="ny_sla", url=SODA_URL, dest_path=str(dest), row_count=len(records),
              notes=f"license_classes={BREWERY_LICENSE_CLASSES}; excludes 'CM' Combined Craft "
                    "Status (administrative overlay, verified to duplicate a base brewer-class "
                    "record at the same address in every case spot-checked)")
    return dest


def load() -> pd.DataFrame:
    """Load the cached export, collapse to one row per physical location, geocode gaps."""
    path = fetch()
    records = json.loads(path.read_text())
    n0 = len(records)

    rows = []
    for r in records:
        geo = r.get("georeference") or {}
        coords = geo.get("coordinates") or [None, None]
        lon, lat = coords[0], coords[1]
        street = r.get("actualaddressofpremises")
        rows.append({
            "license_id": r.get("licensepermitid"),
            "legal_name": r.get("legalname"),
            "dba": r.get("dba"),
            "license_class": r.get("class"),
            "license_class_desc": r.get("description"),
            "street_address": street,
            "city": r.get("city"),
            "county": r.get("premisescounty"),
            "state": "NY",
            "zip": r.get("zipcode"),
            "lat": float(lat) if lat is not None else None,
            "lon": float(lon) if lon is not None else None,
            "_addr_key": (street or "").strip().upper() + "|" + (r.get("zipcode") or "").strip(),
        })
    df = pd.DataFrame(rows)

    n_has_addr = (df["_addr_key"] != "|").sum()
    log_filter("ny_sla", "has a street address", n0, int(n_has_addr))

    # Prefer rows that already carry coordinates when collapsing duplicate addresses,
    # so the geocoder fallback below only has to cover genuinely coordinate-less locations.
    df = df.sort_values(by="lat", na_position="last")
    deduped = df.drop_duplicates(subset="_addr_key", keep="first").drop(columns="_addr_key")
    deduped = deduped.reset_index(drop=True)
    log_filter("ny_sla", "dedupe to one row per physical (address, zip) location",
               n0, len(deduped),
               notes="collapses same-licensee multi-class records (e.g. Farm Brewer + "
                     "Micro-Brewer at one brewery) and contract-brewing / co-packing "
                     "arrangements (multiple brand licensees at one shared facility)")

    deduped["ny_sla_id"] = deduped.index

    from breweries.geocode import fill_missing_coords

    try:
        deduped = fill_missing_coords(
            deduped, "ny_sla_id", "lat", "lon", "street_address", "city", "state", "zip", "ny_sla"
        )
    except KeyError:
        # breweries.geocode.fill_missing_coords -> census_geocoder.geocode_addresses hits a
        # latent bug when a whole geocoding batch returns zero matches: the "coordinates"
        # response column comes back empty, str.split(",") yields a single column, and
        # coords[1] raises KeyError. Verified directly against the Census Geocoder API that
        # all 3 NY rows missing coordinates (rural/hamlet addresses: Watkins Glen, Cherry
        # Plain, Dresden) genuinely return "No_Match" — not a transient failure. Not fixed
        # here since census_geocoder.py is shared infrastructure another agent owns; NY
        # already carries a state-supplied `county` (premisescounty) for these rows, so they
        # are not lost, only left without lat/lon for the TIGER-based spatial join that
        # assign_geographies() does for every other source.
        still_missing = deduped["lat"].isna() & deduped["street_address"].notna()
        log_filter("ny_sla", "Census Geocoder fallback for missing lat/lon",
                   len(deduped), len(deduped),
                   notes=f"attempted={int(still_missing.sum())} recovered=0 "
                         f"still_missing={int(still_missing.sum())} "
                         "(geocoder returned No_Match for all remaining rows; "
                         "state-supplied county retained for these rows)")

    return deduped
