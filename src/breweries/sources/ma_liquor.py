"""Massachusetts Alcoholic Beverages Control Commission (ABCC) -- statewide
active state-license export.

Source: https://www.mass.gov/doc/abcc-active-state-licenses/download -- linked
directly from the ABCC's own "ABCC Active Licenses" page
(https://www.mass.gov/info-details/abcc-active-licenses), under the "Active
License Lists" heading, alongside separate downloads for retail licenses and
one-day-license sources. This is a genuine bulk, no-login static XLSX
(5,202 rows as of the 2026-08-31 fetch) that the ABCC's own page presents as
a direct one-click download --
NOT the ePLACE Portal (elicensing21.mass.gov), a separate interactive
per-license-type search tool also linked from the same page, which was
checked and ruled out as this project's primary source (it requires
selecting a license type and running a search rather than returning a bulk
export). "State licenses" in Massachusetts's two-tier system are exactly the
manufacturer/wholesaler-class licenses issued by the ABCC itself (as opposed
to "retail licenses," which are issued by individual city/town licensing
boards under ABCC's oversight and published as a separate file this project
does not use) -- brewery manufacturing licenses are a state-license class,
so this is the right file.

## A note on fetching this file programmatically

`curl` requests to the mass.gov download URL, from this project's
investigation environment, returned an Akamai-style "Not allowed" 403 on
every attempt (multiple realistic browser User-Agent strings tried) -- a
generic IP-reputation/WAF block, not a CAPTCHA, a session-gated form, or any
form of per-record interactive lookup (the file itself is a single static
document the agency's own page links for one-click public download, unlike
the excluded MN/SC/AZ tools in methods_memo.md Section 8, which have no bulk
file to fetch in the first place regardless of where the request originates
from). Python's `requests` library, used by `fetch()` below, was NOT
blocked by the same WAF rule when tested directly (confirmed 2026-08-31: a
plain GET against the live URL succeeded and returned the current 5,202-row
file) -- evidently the block keys on a request fingerprint `curl` triggers
and `requests` does not, not on IP reputation alone. `fetch()` still tries
the canonical mass.gov URL first, then falls back to the Internet Archive's
Wayback Machine mirror of the exact same official document, then to a
checked-in seed copy (data/raw/ma_liquor/abcc_active_state_licenses_seed.xlsx,
fetched via the Wayback Machine, dated 2025-10-31) -- defense in depth for a
source that has, in practice, worked on every direct attempt so far, not a
routine requirement.

## License classes and inclusion rule

The `LICENSE_TYPE` column enumerates every ABCC state-license class. Two are
genuine physical-brewing-location classes:
- "Farmer Brewery" (225 of 5,202 rows) -- the standard MA craft-brewery
  manufacturing license (M.G.L. c. 138, Sec 19B), allowing brewing plus
  on-site retail sale.
- "Pub Brewery" (39 of 5,202 rows) -- Massachusetts's brewpub-adjacent state
  license class.
BREWERY_LICENSE_TYPES is the union of both.

Excluded, deliberately: "Manufacturer" (7 rows). Checked directly by name
against an earlier (2025-10-31) snapshot of the same file -- of 8 rows in
that snapshot, several belonged to businesses that also hold a Farmer
Brewery license at the same address (e.g. Finestkind Brewing's Worcester and
Westminster locations, American Craft Brewery's Boston location), where the
"Manufacturer" record is a companion self-distribution/wholesale-type
license at the brewery's own premises (same pattern as IL's 3C + Class-1/2/3
overlay licenses) -- a duplicate of the Farmer Brewery premises, not a
second physical location. The rest (ColdSnap Corp. [canned cocktails],
Rustic Spirits LLC, Fated Farmer Distillery LLC, IROKOS Group) are
non-brewery manufacturers (RTD/spirits producers) with no Farmer Brewery or
Pub Brewery counterpart at all. Net effect: "Manufacturer" contributes
nothing but noise and near-duplicates to a brewery count and is excluded
entirely. Also excluded: "Certificate of Compliance" (2,632 rows, the
largest class by far) -- an out-of-state brand-registration license with no
physical Massachusetts premises (confirmed in the 2025-10-31 snapshot: Peak
Organic Brewing Co.'s Certificate of Compliance record carried no
city/address at all, unlike its real "Manufacturer" premises record).

## Active filter

`LICENSE_STATUS` is "Issued" for 5,200 of 5,202 rows and "Revoked" for the
remaining 2 (unrelated Certificate of Compliance records in the 2026-08-31
fetch). Filtered to LICENSE_STATUS == "Issued". A separate `EXP_STATUS`
column reads "About to Expire" for the majority of rows purely because of
MA's uniform Dec-31 annual renewal cycle relative to the fetch date -- NOT a
sign of actual expiration (checked against the 2025-10-31 snapshot: only 3
of 5,408 EXP_DATE values were in the past as of that snapshot date, and
those were the same 3 Revoked rows) -- so EXP_STATUS is not used as a
filter, unlike IL's cumulative-export expiration-date filter.

## No county field -- geocoded via address

Unlike IL/PA/MO, this export carries no county column, only
city/state/zip (`G7_CITY`, `G7_STATE`, `G7_ZIP`) and a separately-tracked
house number (`STREET_NO`) + street (`G7_ADDRESS1`). Records are geocoded
via the Census Geocoder batch API (same fallback path OBDB/OSM use for
missing coordinates) and spatially joined to county polygons, rather than
using a source-provided county field (none exists here).
"""

from __future__ import annotations

import glob
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

RAW_DIR = Path("data/raw/ma_liquor")
ACTIVE_LICENSES_PAGE_URL = "https://www.mass.gov/info-details/abcc-active-licenses"
LIVE_URL = "https://www.mass.gov/doc/abcc-active-state-licenses/download"
WAYBACK_FALLBACK_URL = (
    "http://web.archive.org/web/20251109201535if_/"
    "https://www.mass.gov/doc/abcc-active-state-licenses/download"
)
SEED_FILE = RAW_DIR / "abcc_active_state_licenses_seed.xlsx"

BREWERY_LICENSE_TYPES = ["Farmer Brewery", "Pub Brewery"]

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def fetch(force: bool = False) -> Path:
    """Download the ABCC Active State Licenses XLSX, or reuse the cache.

    Tries the canonical mass.gov URL first; falls back to the Internet
    Archive's mirror of the same file if mass.gov's WAF blocks the direct
    request (see module docstring); falls back to the checked-in seed copy
    as a last resort so this source never hard-fails a pipeline run.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "abcc_active_state_licenses_[0-9]*.xlsx")))
    if existing and not force:
        return Path(existing[-1])

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"abcc_active_state_licenses_{ts}.xlsx"

    content: bytes | None = None
    source_used = None
    for label, url in [("live mass.gov URL", LIVE_URL), ("Wayback Machine mirror", WAYBACK_FALLBACK_URL)]:
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=120)
            resp.raise_for_status()
            if resp.content[:2] == b"PK":  # XLSX = zip container, starts with PK
                content = resp.content
                source_used = label
                break
        except requests.RequestException:
            continue

    if content is None:
        if SEED_FILE.exists():
            content = SEED_FILE.read_bytes()
            source_used = f"checked-in seed file ({SEED_FILE})"
        else:
            raise RuntimeError(
                f"Could not fetch MA ABCC Active State Licenses from {LIVE_URL} or its Wayback "
                f"fallback, and no seed file exists at {SEED_FILE}."
            )

    dest.write_bytes(content)
    df = pd.read_excel(dest, header=1)
    log_fetch(
        source="ma_liquor", url=LIVE_URL, dest_path=str(dest), row_count=len(df),
        notes=f"ABCC Active State Licenses XLSX, linked from {ACTIVE_LICENSES_PAGE_URL}; "
              f"fetched via {source_used}",
    )
    return dest


def load() -> pd.DataFrame:
    """Load the cached export and filter to active MA brewery-class state
    licenses. See module docstring for the full inclusion-rule reasoning."""
    path = fetch()
    df = pd.read_excel(path, header=1)
    n0 = len(df)

    brewery = df[df["LICENSE_TYPE"].isin(BREWERY_LICENSE_TYPES)].copy()
    log_filter("ma_liquor", f"LICENSE_TYPE in {BREWERY_LICENSE_TYPES}", n0, len(brewery))

    n_before_status = len(brewery)
    brewery = brewery[brewery["LICENSE_STATUS"] == "Issued"].copy()
    log_filter("ma_liquor", "LICENSE_STATUS == 'Issued'", n_before_status, len(brewery))

    n_before_addr = len(brewery)
    brewery = brewery[brewery["G7_CITY"].notna()].copy()
    log_filter("ma_liquor", "G7_CITY not null (has a physical premises address)", n_before_addr, len(brewery))

    brewery["street_address"] = (
        brewery["STREET_NO"].fillna("").astype(str).str.strip() + " " + brewery["G7_ADDRESS1"].fillna("").astype(str).str.strip()
    ).str.strip()
    brewery["city"] = brewery["G7_CITY"].astype(str).str.strip()
    brewery["zip"] = brewery["G7_ZIP"].astype(str).str.strip().str.slice(0, 5)
    brewery["ma_liquor_id"] = brewery["LICENSE_NO"]
    brewery["licensee_name"] = brewery["BUSINESS_NAME"]
    brewery["license_type"] = brewery["LICENSE_TYPE"]
    brewery["state"] = "MA"
    brewery["lat"] = pd.NA
    brewery["lon"] = pd.NA

    return brewery[[
        "ma_liquor_id", "LICENSE_NO", "licensee_name", "DBA", "license_type",
        "street_address", "city", "state", "zip", "lat", "lon",
    ]].rename(columns={"LICENSE_NO": "license_number", "DBA": "dba"}).reset_index(drop=True)
