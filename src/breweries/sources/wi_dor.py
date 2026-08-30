"""Wisconsin Department of Revenue, Division of Alcohol Beverages (DAB) — statewide
fermented malt beverage (beer) permit list.

Source: https://www.revenue.wi.gov/Pages/ISE/excise.aspx ("Current State Permittee
Listing" section), direct-download Excel export:
https://www.revenue.wi.gov/DORReports/beer-permit-list.xlsx

This is Wisconsin's *state*-issued permit roll — manufacturer (Brewer's Permit,
Wis. Stat. 125.29) and Brewpub Permit (Wis. Stat. 125.295) holders, issued by
DOR/DAB directly, distinct from the roughly 15,000 *retail* Class A/B beer and
liquor licenses issued by individual municipalities (cities/villages/towns)
under Wis. Stat. ch. 125, which are not centrally published in bulk and are
out of scope here (see the DOR "Retail Alcohol Beverage License Search" — an
interactive per-municipality search tool, not a bulk export, so it was not
used). This file is a genuine bulk statewide export refreshed periodically by
DOR, not a scrape of an interactive tool.

The workbook has one sheet with a title row, then column headers on row 2
("Account Sub-Type", "Beginning Effective Date", "BTR Expiration Date",
"Business Name", "Business Address", "Business City", "Business State",
"Business ZIP"). "Account Sub-Type" carries four values statewide:

- "Brewery" — manufacturer (Brewer's Permit), a physical WI brewing location.
- "Brewpub" — Brewpub Permit, a physical WI brewing + on-site restaurant.
- "OS Shipper Of Beer" — out-of-state brewers/importers permitted to ship INTO
  Wisconsin; no WI brewing location. Excluded (analogous to GA DOR's
  out-of-state BREWERY shippers).
- "WI Beer Wholesale/Import" — beer wholesalers/distributors. Excluded (not a
  manufacturer).

BREWERY_LICENSE_TYPES = ["Brewery", "Brewpub"] is therefore the brewery-relevant
filter. One Brewery-type row in the current export (Mark Anthony Brewing Inc.,
Chicago, IL — the maker of Mike's Hard Lemonade/White Claw) lists a Chicago,
IL business address despite holding a WI Brewer's Permit; it is dropped by the
addr_state != 'WI' check (same out-of-state-manufacturer judgment call GA made)
rather than by Account Sub-Type. Rows with a null Business State (~5% of
Brewery/Brewpub rows in the current export — the file simply omits address
fields for those permittees, e.g. Badger State Brewing Company, Pilot Project
Brewing Milwaukee) are kept, not dropped, since a missing address is not
evidence of an out-of-state location; they pass through with null lat/lon for
the Census Geocoder fallback to (fail to) resolve, and are logged, not
silently lost. All permits in the current export have a BTR Expiration Date in
the future, but an expiration-date filter is applied anyway for reproducibility
against future re-fetches, since DOR does not purge lapsed permits from the
list immediately.

No lat/lon or county field is supplied. Business Address/City/State/ZIP route
through breweries.geocode.fill_missing_coords (Census Geocoder batch fallback),
same mechanism as ga_dor.py.
"""

from __future__ import annotations

import glob
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

RAW_DIR = Path("data/raw/wi_dor")
REPORT_PAGE_URL = "https://www.revenue.wi.gov/Pages/ISE/excise.aspx"
DOWNLOAD_URL = "https://www.revenue.wi.gov/DORReports/beer-permit-list.xlsx"

BREWERY_LICENSE_TYPES = ["Brewery", "Brewpub"]


def fetch(force: bool = False) -> Path:
    """Download the DOR statewide fermented malt beverage permit list, or reuse the cache."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "beer_permit_list_*.xlsx")))
    if existing and not force:
        return Path(existing[-1])

    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    resp = requests.get(DOWNLOAD_URL, headers=headers, timeout=120)
    resp.raise_for_status()
    if not resp.content.startswith(b"PK"):
        raise RuntimeError("Expected an .xlsx response from WI DOR beer-permit-list; got something else.")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"beer_permit_list_{ts}.xlsx"
    dest.write_bytes(resp.content)

    df = pd.read_excel(dest, header=1)
    log_fetch(source="wi_dor", url=DOWNLOAD_URL, dest_path=str(dest), row_count=len(df),
              notes=f"statewide DOR/DAB fermented malt beverage permit list, all account "
                    f"sub-types (report page: {REPORT_PAGE_URL})")
    return dest


def load() -> pd.DataFrame:
    """Load the cached export, filter to brewery-relevant permit types, active permits,
    and in-state (or address-unknown) addresses.

    Returns one row per permittee with street/city/state/zip and empty lat/lon
    columns for the Census Geocoder fallback — no coordinates are supplied
    directly by this source.
    """
    path = fetch()
    df = pd.read_excel(path, header=1)
    n0 = len(df)

    brewery = df[df["Account Sub-Type"].isin(BREWERY_LICENSE_TYPES)].copy()
    log_filter("wi_dor", f"Account Sub-Type in {BREWERY_LICENSE_TYPES}", n0, len(brewery))

    n_before_expiry = len(brewery)
    now = pd.Timestamp.now()
    active = brewery[brewery["BTR Expiration Date"] >= now].copy()
    log_filter("wi_dor", "BTR Expiration Date >= today (active permits only)",
               n_before_expiry, len(active))

    n_before_state = len(active)
    # Drop confirmed out-of-state manufacturers (e.g. Mark Anthony Brewing Inc.,
    # Chicago IL, holds a WI Brewer's Permit but has no WI brewing location).
    # Rows with a missing Business State are kept — a blank address field is not
    # evidence the permittee is out of state, just an incomplete DOR record.
    out_of_state_mask = active["Business State"].notna() & (active["Business State"] != "WI")
    in_state = active[~out_of_state_mask].copy()
    log_filter("wi_dor", "drop confirmed out-of-state Business State (keep unknown/blank)",
               n_before_state, len(in_state),
               notes="out-of-state Brewery/Brewpub permittees hold a WI manufacturer permit "
                     "but have no physical WI brewing location")

    in_state = in_state.reset_index(drop=True)
    in_state["wi_dor_id"] = in_state.index
    in_state["zip"] = in_state["Business ZIP"].astype(str).str.replace(r"\.0$", "", regex=True).str[:5]
    in_state["lat"] = pd.NA
    in_state["lon"] = pd.NA

    out = in_state.rename(columns={
        "Account Sub-Type": "license_type",
        "Beginning Effective Date": "beginning_effective_date",
        "BTR Expiration Date": "btr_expiration_date",
        "Business Name": "licensee_name",
        "Business Address": "street_address",
        "Business City": "city",
        "Business State": "state",
    })

    return out[[
        "wi_dor_id", "licensee_name", "license_type", "beginning_effective_date",
        "btr_expiration_date", "street_address", "city", "state", "zip", "lat", "lon",
    ]]
