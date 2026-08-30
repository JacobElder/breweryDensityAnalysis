"""Washington State Liquor and Cannabis Board (WSLCB) licensee list — brewery
calibration source.

Source: https://licensinginfo.lcb.wa.gov/LicenseeListDetails.asp?typeLic=326
(license type 326, "Washington Domestic and Microbreweries"), linked directly
from the WSLCB's own public "Licensee List" page
(https://lcb.wa.gov/taxreporting/licensee-list). This is the state alcohol
regulator's own bulk export tool: one GET request parameterized by license
*type code* returns the complete, statewide roster for that category (436
records as of 2026-08-30) with a `Content-Disposition: attachment` Excel-typed
response — not a per-business/per-address search. It is the same kind of
source as MI LARA's Master License List (a full agency-published register, one
request per category, no bot-block encountered) and is the closest thing WSLCB
offers to CO/OR's Socrata bulk APIs.

data.wa.gov (Socrata) was checked first, per the project's default assumption
that WA would have a CO/OR-style open-data portal dataset. It does not: the
only WSLCB-tagged datasets on data.wa.gov are "Liquor Renewal" (a *rolling
~2.5-month window* of upcoming renewal notices — 4,468 rows total across all
license types, all states, as of this fetch; confirmed via
`$select=min(renewaldate),max(renewaldate)` returning 2026-04-15..2026-06-30,
not a full current-license snapshot) and "Local Authority Letters" (10 rows,
with a companion view literally named "[To be removed from public view] LCB
Local Authority Letters" — evidently being sunset). Neither is a usable bulk
roster, so this module uses the WSLCB's own licensee-list export instead.

The HTML response has no `lxml`/`bs4`/`html5lib` available in this project's
environment (see pyproject.toml), so it is parsed with a small `<td>` regex
rather than `pandas.read_html` — the table markup is simple and regular
(6 columns, no nested tags), and every row is logged in the manifest with a
before/after count so a parsing regression would be visible immediately.

WSLCB's own record-level table has no lat/lon or county, only street address /
city / state / zip, so records are geocoded downstream via the project's
Census Geocoder fallback (breweries.geocode.fill_missing_coords), the same
path OBDB already uses.

Inclusion rule: type 326 ("Washington Domestic and Microbreweries") is the
only brewery-manufacturing category on WSLCB's list — everything else with
"Beer"/"Brew" in its label (Beer Distributor, Beer Importer, Beer COA, etc.)
is a wholesale/distribution-tier license, not a brewing location, and is
excluded, mirroring how CO/OR exclude distributor licenses. WSLCB's other
candidate category, type 470 "Washington Public House" — the closest thing to
a separate brewpub license, analogous to CO's "Brew Pub" split — currently has
zero active licensees; WA brewpubs instead hold the same type-326 brewery
license plus a separate restaurant/on-premises endorsement (visible in the
`data.wa.gov` renewal sample as e.g. "Microbrewery" + "B/W Restaurant -
Beer/Wine" on one license), so there is no second license-type bucket to
combine here the way there is for CO/OR.
"""

from __future__ import annotations

import glob
import html
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

RAW_DIR = Path("data/raw/wa_liquor")
LICENSEE_LIST_URL = "https://licensinginfo.lcb.wa.gov/LicenseeListDetails.asp"
BREWERY_TYPE_CODE = "326"
BREWERY_TYPE_LABEL = "Washington Domestic and Microbreweries"

_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_cell(raw: str) -> str:
    text = _TAG_RE.sub("", raw)
    text = html.unescape(text).replace("\xa0", " ")
    return text.strip()


def fetch(force: bool = False) -> Path:
    """Download the WSLCB type-326 (brewery) licensee list, or reuse the cache."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "wa_breweries_*.html")))
    if existing and not force:
        return Path(existing[-1])

    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    resp = requests.get(
        LICENSEE_LIST_URL,
        params={"typeLic": BREWERY_TYPE_CODE, "PrivDesc": BREWERY_TYPE_LABEL},
        headers=headers,
        timeout=60,
    )
    resp.raise_for_status()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"wa_breweries_{ts}.html"
    dest.write_bytes(resp.content)

    rows = _parse_rows(resp.text)
    log_fetch(source="wa_liquor", url=resp.url, dest_path=str(dest), row_count=len(rows),
              notes=f"license_type={BREWERY_TYPE_CODE} ({BREWERY_TYPE_LABEL})")
    return dest


def _parse_rows(text: str) -> list[list[str]]:
    """Parse the licensee table's flat <td> stream into 6-column rows.

    Layout: 1 title cell (colspan=6) + 6 header cells, then N*6 data cells
    (License #, Licensee, Address, City, State, Zip). Regex-based rather than
    pandas.read_html because lxml/bs4/html5lib are not project dependencies.
    """
    cells = [_clean_cell(c) for c in _TD_RE.findall(text)]
    body = cells[7:]  # drop 1 title cell + 6 header-label cells
    n_complete_rows = len(body) // 6
    if n_complete_rows * 6 != len(body):
        # Never silently truncate a partial trailing row — surface it instead.
        raise ValueError(f"WA licensee table cell count ({len(body)}) is not a multiple of 6")
    return [body[i * 6:(i + 1) * 6] for i in range(n_complete_rows)]


def load() -> pd.DataFrame:
    """Load the WSLCB brewery licensee list as a DataFrame with (initially null)
    lat/lon columns, ready for breweries.geocode.fill_missing_coords.
    """
    path = fetch()
    rows = _parse_rows(path.read_text())
    n0 = len(rows)

    df = pd.DataFrame(rows, columns=["license_number", "licensee_name", "street_address", "city", "state", "zip"])

    has_name_and_address = df["licensee_name"].str.len().gt(0) & df["street_address"].str.len().gt(0)
    df = df[has_name_and_address].reset_index(drop=True)
    log_filter("wa_liquor", "has non-empty licensee name and street address", n0, len(df))

    df["lat"] = pd.NA
    df["lon"] = pd.NA
    df["lat"] = pd.to_numeric(df["lat"])
    df["lon"] = pd.to_numeric(df["lon"])

    return df
