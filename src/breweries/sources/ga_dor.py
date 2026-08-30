"""Georgia Department of Revenue, Alcohol & Tobacco Division — Active Alcohol Licenses.

Source: https://dor.georgia.gov/active-alcohol-licenses — a quarterly-refreshed
bulk Excel export ("Alcohol Accounts Active <Month> <Year>.xlsx") of every active
alcohol account/license in the state, direct-download link (not an interactive
search form), currently:
https://dor.georgia.gov/document/document/alcohol-accounts-active-june-2026xlsx/download

This is record-level and includes a "License Type" column with a dedicated
BREWERY category (manufacturer license) and BREWPUB category, distinct from
RETAIL, CONSUMPTION, WHOLESALER, DISTILLERY, WINERY, IMPORTER, BROKER, etc. —
Georgia's own answer to the manufacturer-vs-retail distinction this project
looks for in every calibration state.

There is no lat/lon or county field. "List Format Address" is a single combined
string (e.g. "195 OTTLEY DR NE ATLANTA GA 30324-3924"); this module regex-parses
the trailing " STATE ZIP" off the end (reliable — tested against all 253
BREWERY/BREWPUB rows, zero parse failures) and leaves the remainder, including
the embedded city name, as one "street_address" field for the Census Geocoder
batch fallback (breweries.geocode.fill_missing_coords), the same mechanism OBDB
already uses for coordinate-less records. Blank city + correct state/zip matched
147/172 in-state test rows (85.5%), comparable to OBDB's own geocoder fallback
rate for NC.

A number of BREWERY-type licensees are out-of-state manufacturers (e.g.
Anheuser-Busch's Cartersville brewery *is* in GA, but Westbrook Brewing Co. in
Mount Pleasant, SC, and Southern Tier in Lakewood, NY, also hold GA BREWERY
licenses to ship into the state — they have no physical Georgia brewing
location). Filtered out via the parsed state != 'GA' check, mirroring the
project's "pure distributors/importers out" judgment call: a license to sell
into Georgia is not a physical Georgia brewing location.
"""

from __future__ import annotations

import glob
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

RAW_DIR = Path("data/raw/ga_dor")
REPORT_PAGE_URL = "https://dor.georgia.gov/active-alcohol-licenses"
DOWNLOAD_URL = "https://dor.georgia.gov/document/document/alcohol-accounts-active-june-2026xlsx/download"

BREWERY_LICENSE_TYPES = ["BREWERY", "BREWPUB"]

_ADDR_RE = re.compile(r"^(?P<street>.+?)\s+(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$")


def fetch(force: bool = False) -> Path:
    """Download the DOR active-alcohol-licenses quarterly Excel export, or reuse the cache."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "alcohol_accounts_active_*.xlsx")))
    if existing and not force:
        return Path(existing[-1])

    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    resp = requests.get(DOWNLOAD_URL, headers=headers, timeout=120)
    resp.raise_for_status()
    if not resp.content.startswith(b"PK"):
        raise RuntimeError("Expected an .xlsx response from GA DOR active-alcohol-licenses; got something else.")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"alcohol_accounts_active_{ts}.xlsx"
    dest.write_bytes(resp.content)

    df = pd.read_excel(dest)
    log_fetch(source="ga_dor", url=DOWNLOAD_URL, dest_path=str(dest), row_count=len(df),
              notes="full statewide active alcohol accounts, all license types, quarterly export "
                    f"(report page: {REPORT_PAGE_URL})")
    return dest


def _parse_address(addr: object) -> tuple[str | None, str | None, str | None]:
    if not isinstance(addr, str):
        return None, None, None
    m = _ADDR_RE.match(addr.strip())
    if not m:
        return None, None, None
    return m.group("street"), m.group("state"), m.group("zip")


def load() -> pd.DataFrame:
    """Load the cached export, filter to brewery-relevant license types and in-state addresses.

    Returns one row per licensee with a street_address (city baked in, per the
    module docstring), state, zip, and empty lat/lon columns for the Census
    Geocoder fallback — no coordinates are supplied directly by this source.
    """
    path = fetch()
    df = pd.read_excel(path)
    n0 = len(df)

    brewery = df[df["License Type"].isin(BREWERY_LICENSE_TYPES)]
    log_filter("ga_dor", f"License Type in {BREWERY_LICENSE_TYPES}", n0, len(brewery))

    parsed = brewery["List Format Address"].apply(
        lambda a: pd.Series(_parse_address(a), index=["street_address", "addr_state", "zip"])
    )
    brewery = pd.concat([brewery.reset_index(drop=True), parsed.reset_index(drop=True)], axis=1)

    n_before_state = len(brewery)
    in_state = brewery[brewery["addr_state"] == "GA"].copy()
    log_filter("ga_dor", "addr_state == 'GA' (drop out-of-state manufacturer/shipping licenses)",
               n_before_state, len(in_state),
               notes="out-of-state BREWERY/BREWPUB licensees hold a GA license to ship into the "
                     "state but have no physical GA brewing location")

    in_state = in_state.reset_index(drop=True)
    in_state["ga_dor_id"] = in_state.index
    in_state["city"] = ""
    in_state["state"] = "GA"
    in_state["zip"] = in_state["zip"].astype(str).str.split("-").str[0]
    in_state["lat"] = pd.NA
    in_state["lon"] = pd.NA

    out = in_state.rename(columns={
        "List Format Name": "licensee_name",
        "License Type": "license_type",
        "Account Commence Date": "account_commence_date",
        "Local Lic Location": "local_lic_location",
    })

    return out[[
        "ga_dor_id", "licensee_name", "license_type", "account_commence_date",
        "street_address", "city", "state", "zip", "local_lic_location", "lat", "lon",
    ]]
