"""Missouri Division of Alcohol and Tobacco Control (ATC) -- statewide primary
alcohol-license export, published via the state's Socrata open-data portal.

Source: https://data.mo.gov/resource/d9fr-pncw.json (dataset landing page:
https://data.mo.gov/Regulatory/Missouri-Primary-Alcohol-Licenses/d9fr-pncw) --
linked directly from ATC's own "Reports" page
(https://atc.dps.mo.gov/reports/), which describes it and three sibling
datasets (an active-license view without contact info, a primary/secondary
combined view, and the wholesaler list) as licensing data "posted on the
Missouri Data Portal that allows users to sort, filter and download
information." This is a genuine bulk, no-login Socrata dataset with a
documented REST API (Socrata SODA) -- not the ATC's own site search or any
interactive per-record lookup tool (no such tool was found on atc.dps.mo.gov;
the agency's whole public-facing licensing-data story runs through this
Socrata portal). The dataset's own description ("Current license information
of businesses involved in the manufacture, shipping, and/or sale of alcohol
in the State of Missouri") and its `rowsUpdatedAt` metadata timestamp
(resolving to 2026-08-... at fetch time) both indicate an actively-refreshed,
current-license feed, not a historical/cumulative roll -- confirmed directly:
of 15,722 total rows fetched 2026-08-31, only 3 have LICENSE_STATUS ==
"Revoked" (all Certificates of Compliance, an unrelated out-of-state
brand-registration license class -- see below), so no separate
active-vs-expired date filter is needed the way IL's daily export required
one.

## License classes and inclusion rule

The dataset's `primary_type` field enumerates every Missouri alcohol license
class. Exactly one class is a genuine physical-brewing-location class:
"Microbrewery" (65 of 15,722 rows as of the 2026-08-31 fetch), Missouri's
license for craft breweries producing beer for on-site retail sale and
self-distribution (RSMo 311.196). BREWERY_LICENSE_TYPES = {"Microbrewery"}.

## Critical finding: large/industrial breweries are NOT enumerable from this
## dataset under any primary_type

Checked directly: Anheuser-Busch's St. Louis brewery complex and Boulevard
Brewing's Kansas City complex both appear in the raw export, but neither
holds a "Microbrewery" (or any other manufacturer-class) primary_type record
-- each holds only "Retail by Drink" (their taproom/hospitality-center retail
privilege) and solicitor-class licenses ("Liquor Manufacturer Solicitor",
"22% Manufacturer Solicitor" -- these are self-distribution/sales-rep
licenses, not manufacturing licenses). Missouri's actual brewery-manufacturer
permit for large-scale production appears to sit outside this dataset's
`primary_type` enumeration entirely (plausibly tracked only via the federal
TTB brewer's notice, which this project does not have access to -- see
methods_memo.md Section 8). Net effect: this source systematically excludes
Missouri's handful of large/regional breweries (AB, Boulevard, and any other
non-craft-tier manufacturer) and captures only the state's Microbrewery-class
population -- a real, structural undercount relative to OBDB's broader
micro/brewpub/regional/large/nano definition, not a data-quality artifact to
paper over. Documented here rather than worked around.

## No brewpub-class license

Missouri brewpubs (restaurants that brew on-site) are not captured by a
distinct primary_type either -- they most likely operate under an ordinary
"Retail by Drink" license with no license-level signal distinguishing a
brewing restaurant from a non-brewing one. OBDB's "brewpub" type (46 of MO's
121 obdb_count rows) is therefore essentially uncaptured by this source, on
top of the large-brewery gap above. Both gaps push the raw capture rate well
below 100% by construction, not by omission.

## County field and normalization

The dataset carries its own `county` column (Missouri's own administrative
assignment) -- used directly, no geocoding needed, same precedent as
il_liquor.py / pa_liquor.py. Values are upper-cased with a " COUNTY" suffix
(e.g. "JACKSON COUNTY"), except Missouri's one independent city, which reads
"ST. LOUIS CITY" (no "COUNTY" suffix) -- St. Louis city is a Census
county-equivalent entirely separate from, but confusingly same-named as, St.
Louis County right next to it (mirrors Virginia's independent-city pattern;
see va_abc.py and build_va_county_dataset.py, whose GEOID-crosswalk approach
this state's build script also uses). One raw data-quality quirk found and
fixed: "STE. GENEVIEVE COUNTY " carries a trailing space in the source.
Title-casing the remainder gets every Missouri county name right except two,
matched against the Census TIGER county list the same way IL's fix list was
built: DeKalb, McDonald.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

RAW_DIR = Path("data/raw/mo_liquor")
REPORTS_PAGE_URL = "https://atc.dps.mo.gov/reports/"
DATASET_URL = "https://data.mo.gov/Regulatory/Missouri-Primary-Alcohol-Licenses/d9fr-pncw"
API_URL = "https://data.mo.gov/resource/d9fr-pncw.json"

BREWERY_LICENSE_TYPES = ["Microbrewery"]

_COUNTY_NAME_FIXES = {
    "DEKALB": "DeKalb",
    "MCDONALD": "McDonald",
}

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def fetch(force: bool = False) -> Path:
    """Download the full Missouri Primary Alcohol Licenses dataset via Socrata's
    SODA API, or reuse the cache. The dataset is small enough (~15.7k rows as of
    2026-08-31) to pull in a single request; no pagination needed."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(RAW_DIR.glob("mo_primary_alcohol_licenses_*.json"))
    if existing and not force:
        return existing[-1]

    resp = requests.get(API_URL, headers=_HEADERS, params={"$limit": 200_000}, timeout=120)
    resp.raise_for_status()
    rows = resp.json()
    if not isinstance(rows, list) or not rows or "primary_type" not in rows[0]:
        raise RuntimeError(
            "Expected a JSON array of license records with a 'primary_type' field from "
            "Missouri's Socrata API; got something else -- the dataset's schema or ID may "
            "have changed. Check https://atc.dps.mo.gov/reports/ for the current dataset link."
        )

    import datetime as _dt
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"mo_primary_alcohol_licenses_{ts}.json"
    dest.write_text(json.dumps(rows))

    log_fetch(
        source="mo_liquor", url=API_URL, dest_path=str(dest), row_count=len(rows),
        notes=f"full Missouri Primary Alcohol Licenses dataset via Socrata SODA API, linked from "
              f"ATC's own reports page ({REPORTS_PAGE_URL}); dataset landing page {DATASET_URL}",
    )
    return dest


def _normalize_county(raw: str) -> str:
    raw = raw.strip().upper()
    if raw == "ST. LOUIS CITY":
        return "St. Louis city"
    base = raw[: -len(" COUNTY")] if raw.endswith(" COUNTY") else raw
    base = base.strip()
    fixed = _COUNTY_NAME_FIXES.get(base, base.title())
    return f"{fixed} County"


def load() -> pd.DataFrame:
    """Load the cached export and filter to active Missouri Microbrewery-class
    licenses. See module docstring for the full inclusion-rule reasoning,
    including the large-brewery and brewpub coverage gaps."""
    path = fetch()
    df = pd.DataFrame(json.loads(path.read_text()))
    n0 = len(df)

    brewery = df[df["primary_type"].isin(BREWERY_LICENSE_TYPES)].copy()
    log_filter("mo_liquor", f"primary_type in {BREWERY_LICENSE_TYPES}", n0, len(brewery))

    n_before_state = len(brewery)
    brewery = brewery[brewery["state"].astype(str).str.strip().str.upper() == "MISSOURI"].copy()
    log_filter("mo_liquor", "state == 'Missouri'", n_before_state, len(brewery))

    n_before_county = len(brewery)
    brewery = brewery[brewery["county"].notna() & (brewery["county"].astype(str).str.strip() != "")].copy()
    log_filter("mo_liquor", "county not null/empty", n_before_county, len(brewery))

    brewery["county_name"] = brewery["county"].apply(_normalize_county)
    brewery["street_address"] = (
        brewery.get("street_number", "").fillna("") + " " + brewery.get("street", "").fillna("")
    ).str.strip()
    brewery["city"] = brewery["city"].str.title()
    brewery["mo_liquor_id"] = brewery["primary_license"]
    brewery["licensee_name"] = brewery["licensee"]
    brewery["license_type"] = brewery["primary_type"]
    brewery["state"] = "MO"
    brewery["lat"] = pd.NA
    brewery["lon"] = pd.NA

    return brewery[[
        "mo_liquor_id", "primary_license", "licensee_name", "dbaname", "license_type",
        "street_address", "city", "county_name", "state", "zipcode", "lat", "lon",
    ]].rename(columns={"primary_license": "license_number", "zipcode": "zip"})


def county_counts() -> pd.DataFrame:
    """Convenience aggregate: active Missouri Microbrewery licenses per county-equivalent."""
    df = load()
    counts = df.groupby("county_name").size().rename("mo_liquor_count").reset_index()
    return counts
