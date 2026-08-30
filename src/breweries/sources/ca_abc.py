"""California Department of Alcoholic Beverage Control (ABC) — Daily Data Export.

Source: https://www.abc.ca.gov/licensing/licensing-reports/ ("Daily Data Exports",
refreshed 7am PT) — direct-download bulk file at
https://www.abc.ca.gov/wp-content/uploads/DailyExport-CSV.zip. A genuine,
actively-refreshed statewide extract of every ABC license/application on file
(~129k rows, ~7.3MB zipped as of 2026-08-30), not a scrape of the interactive
License Query System (https://maps.gis.ca.gov/abc/lqs/). A fixed-width version
of the same export exists (documented at
https://www.abc.ca.gov/licensing/licensing-reports/weekly-data-export-fixed-width-layout-definition/,
despite that page's URL slug the export itself is daily); this module uses the
CSV variant. California's own open-data portal (data.ca.gov) publishes only
COVID-era catering/citation datasets from ABC, not the licensee registry, so
it is not usable here.

License Type codes relevant to breweries, per
https://www.abc.ca.gov/licensing/license-types/ and confirmed against the
distribution in the export itself:
  - "01" Beer Manufacturer            (>60,000 bbl/yr; 69 active licenses)
  - "23" Small Beer Manufacturer      (<60,000 bbl/yr, the craft-brewery
                                        workhorse license; 1,106 active)
  - "75" Brewpub-Restaurant           (limited on-site brewing + food service;
                                        95 active)
All three require a physical CA brewing premise. Excluded as NOT physical
in-state brewing locations:
  - "26" Out-of-State Beer Manufacturer's Certificate — lets an out-of-state
    brewery ship into CA; no CA brewing premise (247 active as of this pull).
  - "09"/"10" Beer & Wine Importer / Importer's General, "17" Beer and Wine
    Wholesaler — pure distribution, no brewing.
Every row also carries "Lic or App" ("LIC" = issued license, "APP" = pending
application not yet issued) and "Type Status" (e.g. "ACTIVE", "SUREND"
surrendered, "SUSPEN" suspended, "REVPEN" revocation pending, "PEND"). This
module keeps only Lic or App == "LIC" and Type Status == "ACTIVE": a currently
operating, currently-licensed physical location — applications-in-progress and
surrendered/suspended licenses are dropped, mirroring the project's "physical,
currently-operating brewing location" definition used for every other
calibration state.

The export carries "Prem County" directly on every row (all 1,270 rows in the
2026-08-30 pull had a non-blank county) — no geocoding fallback is needed for
county-level rollups, unlike GA/PA which lack a county field. Premise street
address/city/zip are also carried through for completeness and for any future
point-level (lat/lon) work, which would need the Census Geocoder fallback
(breweries.geocode.fill_missing_coords) since this export has no lat/lon.

Some premises hold more than one of these license types, or multiple brands
share one physical address (California permits "alternating proprietorship"
arrangements — several licensed brewing brands at a single shared brewhouse,
e.g. several rows at 357 E Taylor St, San Jose). These are kept as separate
rows (separate licenses = separate legal brewing operations), matching the
no-address-dedup precedent in co_liquor.py/or_olcc.py; flagged here as a
judgment call rather than silently resolved.
"""

from __future__ import annotations

import glob
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

RAW_DIR = Path("data/raw/ca_abc")
REPORT_PAGE_URL = "https://www.abc.ca.gov/licensing/licensing-reports/"
DOWNLOAD_URL = "https://www.abc.ca.gov/wp-content/uploads/DailyExport-CSV.zip"

BREWERY_LICENSE_TYPES = ["01", "23", "75"]

_COLUMN_MAP = {
    "License Type": "license_type",
    "File Number": "file_number",
    "Lic or App": "lic_or_app",
    "Type Status": "type_status",
    "Type Orig Iss Date": "orig_issue_date",
    "Expir Date": "expir_date",
    "Primary Name": "licensee_name",
    "Prem Addr 1": "street_address",
    " Prem Addr 2": "street_address_2",
    "Prem City": "city",
    " Prem State": "state",
    "Prem Zip": "zip",
    "DBA Name": "doing_business_as",
    "Prem County": "county_name",
    "Prem Census Tract #": "census_tract",
}


def fetch(force: bool = False) -> Path:
    """Download the ABC Daily Data Export CSV zip, extract, and cache the CSV."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "abc_daily_export_*.csv")))
    if existing and not force:
        return Path(existing[-1])

    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    resp = requests.get(DOWNLOAD_URL, headers=headers, timeout=120)
    resp.raise_for_status()
    if not resp.content.startswith(b"PK"):
        raise RuntimeError("Expected a zip response from CA ABC DailyExport-CSV.zip; got something else.")

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise RuntimeError(f"No CSV found inside {DOWNLOAD_URL}; zip contents: {zf.namelist()}")
        csv_bytes = zf.read(names[0])

    df = pd.read_csv(io.BytesIO(csv_bytes), skiprows=1, encoding="utf-8-sig", dtype=str, low_memory=False)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"abc_daily_export_{ts}.csv"
    df.to_csv(dest, index=False)

    log_fetch(source="ca_abc", url=DOWNLOAD_URL, dest_path=str(dest), row_count=len(df),
              notes=f"full statewide ABC license/application extract, all license types, "
                    f"refreshed daily (report page: {REPORT_PAGE_URL})")
    return dest


def load() -> pd.DataFrame:
    """Load the cached export, filter to brewery-relevant license types and active,
    issued licenses.

    Returns one row per active brewery/brewpub license with a county_name column
    (present directly on every row in this source, no geocoding needed) plus
    street address fields for any future point-level work.
    """
    path = fetch()
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=["", " "])
    n0 = len(df)

    df = df.rename(columns=_COLUMN_MAP)
    df["license_type"] = df["license_type"].str.strip()

    brewery = df[df["license_type"].isin(BREWERY_LICENSE_TYPES)]
    log_filter("ca_abc", f"license_type in {BREWERY_LICENSE_TYPES}", n0, len(brewery),
               notes="01=Beer Manufacturer, 23=Small Beer Manufacturer, 75=Brewpub-Restaurant; "
                     "excludes 26 (Out-of-State Beer Manufacturer's Certificate, no CA premise), "
                     "09/10 (importer), 17 (wholesaler)")

    n_before_active = len(brewery)
    active = brewery[
        (brewery["lic_or_app"].str.strip() == "LIC")
        & (brewery["type_status"].str.strip() == "ACTIVE")
    ].copy()
    log_filter("ca_abc", "lic_or_app == 'LIC' and type_status == 'ACTIVE'",
               n_before_active, len(active),
               notes="drops pending applications (APP) and surrendered/suspended/"
                     "revocation-pending licenses — currently-operating physical "
                     "locations only")

    active = active.reset_index(drop=True)
    active["ca_abc_id"] = active["file_number"]
    active["county_name"] = active["county_name"].str.strip().str.title()
    active["city"] = active["city"].str.strip()
    active["street_address"] = active["street_address"].str.strip()
    active["state"] = "CA"
    active["zip"] = active["zip"].str.split("-").str[0]
    active["lat"] = pd.NA
    active["lon"] = pd.NA

    return active[[
        "ca_abc_id", "licensee_name", "doing_business_as", "license_type",
        "orig_issue_date", "street_address", "city", "state", "zip",
        "county_name", "census_tract", "lat", "lon",
    ]]


def county_counts() -> pd.DataFrame:
    """County-level brewery/brewpub license counts, using the county field directly."""
    df = load()
    n0 = len(df)
    counts = df.groupby("county_name", dropna=True).size().rename("ca_abc_count").reset_index()
    n_after = int(counts["ca_abc_count"].sum())
    if n_after != n0:
        log_filter("ca_abc", "groupby county_name (rows with missing county dropped)", n0, n_after)
    return counts
