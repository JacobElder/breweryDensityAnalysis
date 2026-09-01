"""DC Alcoholic Beverage and Cannabis Administration (ABCA) — "ABCA Liquor License
Locations" GIS layer, DC's own open-data portal.

Source: https://opendata.dc.gov/datasets/DCGIS::liquor-licenses (catalog page) backing
the ArcGIS FeatureServer layer at
https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Business_Licensing_and_Grants_WebMercator/FeatureServer/5
-- queried directly here via the standard Esri REST query API (`f=json`, no login, no
API key), which is opendata.dc.gov's normal export mechanism for every dataset it
hosts (the portal's own catalog page advertises "browse the data, download it as a
file ... or build apps using our APIs"). This is DC's ABRA-equivalent bulk licensee
roster: 2,329 active-and-recently-cancelled alcohol licenses citywide as of the
2026-08-31 fetch, each carrying a `CLASS`/`TYPE` license-category field, a `BREW_PUB`
flag, `ADDRESS`, `WARD`, and `STATUS`. It is not the interactive `ABRA Record Search`
tool (abca.dc.gov/page/abra-record-search) -- that tool was not used; this module
queries the underlying GIS feature service the DC GIS team publishes for exactly this
kind of bulk consumption.

DC has no counties -- per src/breweries/state_fips.py / STATE_FIPS_ALL, the entire
District is one Census county-equivalent, "District of Columbia" (FIPS 11001). Every
row in this module therefore gets the same county_name; there is no geocoding or
per-record spatial join to do.

## Inclusion rule: two disjoint license patterns cover DC's brewing locations

DC's licensing scheme does not have a dedicated "Brewery" license class the way IL or
PA do. A brewing operation shows up in this layer one of two ways:

1. **`TYPE == 'Manufacturer'`** -- DC's manufacturer's license, used by any on-site
   producer of alcohol (beer, spirits, or wine) regardless of beverage type. Only 7
   rows carry this TYPE as of the fetch, small enough to classify individually by
   product line (checked against each business's own public description): DC Brau
   Brewing (beer) and Crooked Run Fermentation (a beer/sour-ale producer, an Ivy City
   offshoot of Crooked Run Brewing of Sterling, VA) are genuine brewery/malt-beverage
   producers and kept. Don Ciccio & Figli (amaro/liqueur), Cotton & Reed (rum), Republic
   Restoratives (spirits), Bo & Ivy Distillers (gin), and Alchy Cocktails (RTD spirits
   cocktails) are all spirits distilleries under the same TYPE and are dropped --
   `TYPE` alone does not distinguish beverage category, so this split is a documented,
   individually-verified judgment call, not a field-level filter.
2. **`BREW_PUB == 'CHECKED'`** -- a boolean endorsement field on ordinary retail
   licenses (almost always `TYPE == 'Tavern'` or `'Restaurant'`) marking an on-site
   brewing/brewpub operation layered onto that retail license. 12 rows carry this flag:
   Bluejacket/The Arsenal, Right Proper Brewing (two DC locations: 624 T St NW and 920
   Girard St NE), Red Bear Brewing, Solace Outpost, Other Half Brewing, Atlas Brew
   Works (two DC locations: 1201 Half St SE and the Dad Strength Brewing collab at 600
   Howard Rd SE), Aslin Beer Company, Lost Generation Brewing, Henceforth DC, and Right
   Proper Eckington -- all genuine brewpub-license breweries, kept in full.

Combined: 14 rows (2 manufacturer + 12 brew-pub-flagged), one row per physical
premises -- multi-location brewers (Right Proper x2, Atlas x2) are correctly kept as
separate rows since each is a separate ABRA license at a separate address.

## Data-quality caveat: the export lags the newest openings

Cross-checking against the DC Brewers' Guild's own current member list
(dcbg.org/dccraftbeermap, fetched 2026-08-31) turned up three breweries -- City State
Brewing, Nighthawk Brewery, and Third Hill Brewing -- that do not appear anywhere in
this GIS layer under any TYPE/BREW_PUB combination as of the fetch (`EDITED` field on
the layer tops out around May 2026), suggesting the feed has a multi-month lag for the
newest licensees, the same character of caveat as WI's and TX's already-documented
lag findings. In the other direction, the layer's currency was cross-validated
positively: it correctly excludes Hellbender Brewing Company (Ward 5), independently
confirmed closed as of mid-2026, and does not carry Capitol City Brewing Company or
Bardo Brewing, both also closed -- so within its own multi-month refresh cycle the
data is not simply stale, it reflects real closures, just not the very newest
openings.
"""

from __future__ import annotations

import glob
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

RAW_DIR = Path("data/raw/dc_abra")
CATALOG_PAGE_URL = "https://opendata.dc.gov/datasets/DCGIS::liquor-licenses"
FEATURE_LAYER_URL = (
    "https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/"
    "Business_Licensing_and_Grants_WebMercator/FeatureServer/5/query"
)
OUT_FIELDS = [
    "LICENSE", "APPLICANT", "TRADE_NAME", "CLASS", "ADDRESS", "ZIPCODE",
    "WARD", "STATUS", "TYPE", "BREW_PUB", "EXPIRATION_DATE",
]

# TYPE == 'Manufacturer' rows, classified individually by product line (see module
# docstring). Beer/malt-beverage producers are kept; spirits distilleries are not.
MANUFACTURER_BREWERY_TRADE_NAMES = {"DC Brau Brewing", "Crooked Run Fermentation"}
MANUFACTURER_SPIRITS_EXCLUDED = {
    "Don Ciccio & Figli", "Cotton & Reed", "Republic Restoratives",
    "Bo & Ivy Distillers", "Alchy Cocktails",
}

DC_COUNTY_NAME = "District of Columbia"

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def fetch(force: bool = False) -> Path:
    """Query the ABCA Liquor License Locations FeatureServer for every row with
    TYPE == 'Manufacturer' or BREW_PUB == 'CHECKED', or reuse the cached copy.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "dc_abca_brewery_rows_*.json")))
    if existing and not force:
        return Path(existing[-1])

    params = {
        "where": "TYPE='Manufacturer' OR BREW_PUB='CHECKED'",
        "outFields": ",".join(OUT_FIELDS),
        "returnGeometry": "false",
        "f": "json",
    }
    resp = requests.get(FEATURE_LAYER_URL, params=params, headers=_HEADERS, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    if "features" not in payload:
        raise RuntimeError(
            f"Expected a FeatureServer query response with a 'features' key from "
            f"{FEATURE_LAYER_URL}; got {list(payload.keys())} -- the layer's schema or "
            "URL may have changed."
        )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"dc_abca_brewery_rows_{ts}.json"
    dest.write_text(json.dumps(payload))

    log_fetch(
        source="dc_abra", url=FEATURE_LAYER_URL, dest_path=str(dest),
        row_count=len(payload["features"]),
        notes="ABCA Liquor License Locations FeatureServer, queried for "
              "TYPE='Manufacturer' OR BREW_PUB='CHECKED', via opendata.dc.gov's own "
              f"GIS REST API (catalog page: {CATALOG_PAGE_URL})",
    )
    return dest


def load() -> pd.DataFrame:
    """Load the cached query result and apply the manufacturer-vs-spirits split
    documented in the module docstring. Brew-pub-flagged rows need no further
    filtering -- BREW_PUB='CHECKED' only ever appears on genuine brewing operations.
    """
    path = fetch()
    payload = json.loads(path.read_text())
    rows = [f["attributes"] for f in payload["features"]]
    df = pd.DataFrame(rows)
    n0 = len(df)

    is_manufacturer = df["TYPE"] == "Manufacturer"
    is_brewpub = df["BREW_PUB"] == "CHECKED"

    manufacturer_rows = df[is_manufacturer]
    unclassified = set(manufacturer_rows["TRADE_NAME"]) - MANUFACTURER_BREWERY_TRADE_NAMES - MANUFACTURER_SPIRITS_EXCLUDED
    if unclassified:
        raise RuntimeError(
            f"New/unrecognized TYPE=='Manufacturer' trade name(s) in DC ABCA export: "
            f"{unclassified} -- classify as brewery or non-brewery (spirits/wine) in "
            "MANUFACTURER_BREWERY_TRADE_NAMES / MANUFACTURER_SPIRITS_EXCLUDED before "
            "proceeding, per the module docstring's manual-classification rule."
        )

    keep_manufacturer = df["TRADE_NAME"].isin(MANUFACTURER_BREWERY_TRADE_NAMES) & is_manufacturer
    keep = keep_manufacturer | is_brewpub
    out = df[keep].copy()
    log_filter(
        "dc_abra",
        "TYPE=='Manufacturer' (individually classified as beer/malt-beverage, not "
        "spirits) OR BREW_PUB=='CHECKED'",
        n0, len(out),
        notes=f"kept manufacturer rows: {sorted(MANUFACTURER_BREWERY_TRADE_NAMES)}; "
              f"dropped manufacturer rows (spirits): {sorted(MANUFACTURER_SPIRITS_EXCLUDED)}",
    )

    out["county_name"] = DC_COUNTY_NAME
    out["state"] = "DC"
    out["dc_abra_id"] = out["LICENSE"]
    out = out.rename(columns={
        "APPLICANT": "licensee_name", "TRADE_NAME": "trade_name", "TYPE": "license_type",
        "ADDRESS": "street_address", "ZIPCODE": "zip", "WARD": "ward", "STATUS": "status",
    })
    return out[[
        "dc_abra_id", "LICENSE", "licensee_name", "trade_name", "license_type",
        "street_address", "zip", "ward", "status", "county_name", "state",
    ]]


def county_counts() -> pd.DataFrame:
    """Convenience aggregate: the single-row DC "county" brewery count."""
    df = load()
    counts = df.groupby("county_name").size().rename("dc_abra_count").reset_index()
    return counts
