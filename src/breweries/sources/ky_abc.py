"""Kentucky Department of Alcoholic Beverage Control (ABC) — BELLE Portal public
license-lookup grid, queried by license type — statewide bulk brewery source.

Source: https://abcportal.ky.gov/BelleExternal/LicenseLookup/GetGridData (POST,
form-encoded, JSON response) filtered to LicenseType=MIC ("Microbrewery License").
This is the data endpoint behind the public "LOOKUP" tool on KY ABC's own portal
landing page (https://abcportal.ky.gov/BELLEExternal), described there as "Look up
license(s) of all active alcohol licensees in Kentucky." No login is required to
reach or query this endpoint (verified with a plain unauthenticated POST -- the
page itself loads at HTTP 200 with no auth redirect, and querying by LicenseType
with every other field blank returns the state's FULL microbrewery roster in one
response, not a per-record search). The same tool also exposes a same-filtered
"Export to Excel" action
(GET /BelleExternal/LicenseLookup/LookupLicenseTypeExportToExcel?LicenseType=MIC),
confirmed working the same way, but GetGridData is used here directly because it
returns County as a native field (Excel export only has Site ID/Licensee
Name/DBA/Premises Address) -- same precedent as il_liquor.py and mi_lara.py
preferring a source's own county field over geocoding.

This is distinct from -- and NOT -- KY ABC's separate "Kentucky PRO" brand-registration
lookup (productregistrationonline.com, linked from the same portal page), which is a
product/brand registry, not a physical-premises license registry, and was not used.

## Why this qualifies as bulk, not a session-gated search tool

The BELLE Portal is a Kendo-UI grid backed by ASP.NET MVC JSON endpoints
(`aspnetmvc-ajax` transport), not classic ASP.NET WebForms. Unlike Kansas's KDOR
liquor-licensee search (also investigated for this project; see docs/methods_memo.md),
which is WebForms with `__VIEWSTATE`/`__EVENTVALIDATION` postback state that failed
with "Validation of viewstate MAC failed" on a plain scripted POST -- consistent with
anti-automation protection referenced in that site's CSP (challenges.cloudflare.com)
-- KY's GetGridData endpoint accepted a stateless, cookie-free POST on the first try
and returned the full result set with no CAPTCHA, no session requirement, and no rate
limiting encountered. It is a straightforward JSON API parameterized by license type,
the same category of source as WA LCB's type-code-parameterized GET (wa_liquor.py).

## License type and inclusion rule

KY ABC's license-type catalog (`GET /BelleExternal/ReportGenerator/GetLicenseTypes
?isActive=True`, 55 total types) has exactly two beer-manufacturing-relevant codes:

- **MIC -- "Microbrewery License"**: 97 active premises statewide as of the
  2026-08-31 fetch, spanning 37 counties. Every recognizable KY craft brewery
  checked (Country Boy Brewing, Braxton Brewing, Against the Grain, Apocalypse
  Brew Works, West Sixth Brewing via its DBAs, etc.) appears under this code.
  This is the sole inclusion category.
- **MB -- "Brewer's License"**: only 3 active KY holders (Jim Beam Brands Co.
  in Frankfort, Three Springs Bottling Company LLC in Bowling Green, Ultra Pure
  LLC in Louisville). None of these three is a recognizable craft brewery --
  Jim Beam Brands Co. is Beam Suntory's Frankfort distillery/RTD site, and the
  other two read as beverage co-packing/bottling operations, not brewing
  locations. Kentucky statute uses "Brewer's License" as the large-scale/
  industrial malt-beverage manufacturer class (distinct from the craft-scale
  "Microbrewery License"); with 0 of 3 holders matching any brewery in OBDB's
  Kentucky listing, MB is excluded from BREWERY_LICENSE_TYPES -- the same kind
  of in-state-but-not-a-brewery judgment call IL made for a handful of
  out-of-state manufacturer rows (see il_liquor.py), applied here to
  industrial/non-craft entities instead.

No separate brewpub license class exists in KY's scheme; a Microbrewery License
already authorizes on-site retail sale, so brewpub-style operations are covered
by MIC without a second license-class union (unlike IL's 3C+1C split).

## Active-only by construction

The BELLE Portal's own detail-grid endpoint for a given premises is named
`Get_LicensesIntoGrid_OnlyActive`, and the portal's landing-page copy describes
the LOOKUP tool itself as covering "active alcohol licensees in Kentucky" --
i.e. active-only filtering is applied server-side, not something this module
needs to reproduce. No expiration-date filter is applied here (unlike
il_liquor.py's cumulative export, which needed one).

## No dedup needed

97 MIC rows checked for duplicate PremisesID/SiteID: zero found. A few
licensees (e.g. Country Boy Brewing, Braxton Brewing Company) hold MIC
licenses at multiple distinct premises -- correctly kept as separate rows,
each a genuinely separate physical brewing/taproom location, not a
same-premises companion license the way IL's 3C+Class-1/2/3 overlay was.

## County field

KY ABC's `County` field on each grid row is used directly (e.g. "Jefferson",
"Fayette") -- already in TIGER/Census-compatible title case, no normalization
needed (checked against all 37 counties represented in the 2026-08-31 fetch).
"""

from __future__ import annotations

import glob
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

RAW_DIR = Path("data/raw/ky_abc")
PORTAL_LANDING_URL = "https://abcportal.ky.gov/BELLEExternal"
GRID_DATA_URL = "https://abcportal.ky.gov/BelleExternal/LicenseLookup/GetGridData"

MICROBREWERY_TYPE = "MIC"
BREWERY_LICENSE_TYPES = [MICROBREWERY_TYPE]

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "X-Requested-With": "XMLHttpRequest",
}

_SEARCH_FIELDS = {
    "DBA": "",
    "LicenseeName": "",
    "LicenseNumber": "",
    "SiteID": "",
    "BusinessType": "",
    "PremisesStreet": "",
    "State": "",
    "County": "",
    "Zip": "",
    "City": "",
}


def _fetch_license_type(license_type: str) -> list[dict]:
    form = dict(_SEARCH_FIELDS)
    form["LicenseType"] = license_type
    form["page"] = "1"
    form["pageSize"] = "5000"  # statewide roster is ~100 rows; generous ceiling
    resp = requests.post(GRID_DATA_URL, data=form, headers=_HEADERS, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("Errors"):
        raise RuntimeError(f"KY ABC GetGridData returned errors: {payload['Errors']}")
    rows = payload["Data"]
    total = payload["Total"]
    if len(rows) != total:
        raise RuntimeError(
            f"KY ABC GetGridData for {license_type}: got {len(rows)} rows but Total={total} "
            "-- pageSize ceiling may need raising."
        )
    return rows


def fetch(force: bool = False) -> Path:
    """Download KY ABC's Microbrewery-License roster via the BELLE Portal's public
    GetGridData endpoint, or reuse the cache.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "ky_abc_mic_*.json")))
    if existing and not force:
        return Path(existing[-1])

    rows = _fetch_license_type(MICROBREWERY_TYPE)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"ky_abc_mic_{ts}.json"
    dest.write_text(json.dumps(rows))

    log_fetch(
        source="ky_abc", url=f"{GRID_DATA_URL}?LicenseType={MICROBREWERY_TYPE}", dest_path=str(dest),
        row_count=len(rows),
        notes="KY ABC BELLE Portal public LicenseLookup grid, LicenseType=MIC (Microbrewery License) "
              "only -- active licensees, per the portal's own 'active alcohol licensees' framing; "
              "see module docstring for why MB (Brewer's License, 3 non-craft in-state holders) "
              "is excluded",
    )
    return dest


def load() -> pd.DataFrame:
    """Load the cached KY ABC microbrewery roster as a DataFrame with a native
    county_name column -- no geocoding needed, same precedent as il_liquor.py.
    """
    path = fetch()
    rows = json.loads(path.read_text())
    n0 = len(rows)

    df = pd.DataFrame(rows)

    has_county = df["County"].notna() & (df["County"].str.strip() != "")
    df = df[has_county].copy()
    log_filter("ky_abc", "County is non-empty", n0, len(df))

    n_before_dupe = len(df)
    df = df.drop_duplicates(subset=["PremisesID"]).copy()
    log_filter("ky_abc", "dedup on PremisesID (defensive; none found in 2026-08-31 fetch)",
               n_before_dupe, len(df))

    df["county_name"] = df["County"].str.strip()
    df["ky_abc_id"] = df["PremisesID"].astype(str)
    df["licensee_name"] = df["LicenseeName"]
    df["dba_name"] = df["DBA"]
    df["street_address"] = df["PremisesStreet"]
    df["city"] = df["City"]
    df["state"] = df["State"]
    df["zip"] = df["Zip"]
    df["license_type"] = MICROBREWERY_TYPE
    df["lat"] = pd.NA
    df["lon"] = pd.NA

    return df[[
        "ky_abc_id", "licensee_name", "dba_name", "license_type", "street_address",
        "city", "county_name", "state", "zip", "lat", "lon",
    ]]


def county_counts() -> pd.DataFrame:
    """Convenience aggregate: active KY ABC Microbrewery-License premises per county."""
    df = load()
    counts = df.groupby("county_name").size().rename("ky_abc_count").reset_index()
    return counts
