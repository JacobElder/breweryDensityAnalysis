"""Florida Division of Alcoholic Beverages and Tobacco (DBPR/ABT) — statewide
weekly public-records license extract, "Alcoholic Beverage Manufacturers /
Distributors" (profession code 4005).

Source: https://www2.myfloridalicense.com/sto/file_download/extracts/bd4005lic.csv
This CSV is linked directly from DBPR's own "Public Records" page for
Alcoholic Beverages & Tobacco
(https://www2.myfloridalicense.com/alcoholic-beverages-and-tobacco/public-records/,
"Alcoholic Beverage Manufacturers / Distributors" download) -- a genuine bulk,
no-login, statewide export refreshed weekly, per the site's own disclaimer page
(https://www2.myfloridalicense.com/public-records-read-medisclaimer/) and file
layout reference (https://www2.myfloridalicense.com/sto/documents/readme.pdf,
"AB&T Licensees File Layout" section). This is NOT the interactive "License
Search" tool (myfloridalicense.com/portalsearches/VerifyLicensee), which was
checked and ruled out (single-record lookup only, no bulk export) before this
file was found. There is also a comprehensive all-professions file
(bd400lic.csv) and a county-level "Beverage Licenses Issued By County" annual
PDF report, but the profession-scoped bd4005lic.csv is the right granularity
here: same underlying weekly refresh, pre-filtered to the alcoholic-beverage
manufacturer/distributor population instead of DBPR's ~30 other regulated
professions.

## Columns and scope

One row per license record: Board, Profession, Owner Name, Series (license
type code), Modifier, Mail Address 1-3/City/State/ZIP/County, DBA,
Location Address 1-3/City/State/ZIP/County, License Number, Primary Status,
Secondary Status, Original Licensure Date, Effective Date, Expiration Date,
Tax Stamp Designation, Smoking Designation, Retail Tobacco Indicator. Per
DBPR's own file-layout reference, this profession-4005 extract's scope is
"Active, escrow, temporary and delinquent licenses (null & void, revoked and
transferred records are not included in this download)" -- narrower than the
comprehensive all-professions file (which also carries inactive/cancelled
rows), but this project applies its own Primary Status == '20' ("Current")
filter on top anyway, for the same reproducibility-against-future-fetches
reason wi_dor.py applies an expiration filter even when the current export
happens to be all-active.

## License type ("Series") and inclusion rule

Florida's license_types.pdf (DBPR's official "Licenses And Permits For
Alcoholic Beverages" reference, MANUFACTURERS -- ALCOHOLIC BEVERAGES section)
defines "CMB" as "Manufacturer of Malt Beverages" (Fla. Stat. 563.02(2)):
"Engaged in brewing malt beverages. License permits the manufacture of
alcoholic beverages and the distribution of the same at wholesale..." --
Florida's brewery manufacturer license, the direct equivalent of NC's brewery
permit / WI's Brewer's Permit / IL's "3C - BREWER". Verified against the raw
2026-08-30 fetch: 275 of 332 raw CMB rows have "BREW" in the DBA or Owner Name
(the rest are breweries whose trade name doesn't contain the word, e.g.
Yuengling Brewing Company of Tampa Inc, Dunedin Brewery, Swamp Head Brewery,
plus a handful of Series==CMB winery/cidery-adjacent operations legitimately
licensed to also brew malt beverages) -- CMB is unambiguously the malt-beverage
manufacturer class, not a mixed bucket.

BREWERY_SERIES = ["CMB"] only. Excluded, deliberately, from the same
license_types.pdf: "AMW"/"JDBW" (wine manufacturers/wine-and-cordial
manufacturers), "DD"/"KLD"/"KLD2" (distributor classes), "IMPR" (importer),
"MEXP" (export), "BSA"/"ERB"/"BMWC" (other distributor/broker sub-classes) --
none of these are brewing licenses.

Florida also defines a "CMBP" ("Manufacturer of Malt Beverages in Vendor
Premises," Fla. Stat. 561.221(3)) -- a brewpub-style license "issued in
connection with a primary [retail vendor] license." This is the FL analogue of
IL's "1C - BREW PUB" retail-side class. It was checked for explicitly: zero
CMBP rows exist in either the 4005 (manufacturer/distributor) extract or the
4006 (retail) extract as of the 2026-08-30 fetch. Rather than a missed
population, this appears to reflect how Florida brewpubs actually license
themselves in practice -- a brewpub with retail on-premises sales typically
holds a standard CMB manufacturer license (which permits distribution) plus an
ordinary retail consumption-on-premises license (4COP/2COP/etc.), not the
little-used CMBP class -- so BREWERY_SERIES == ["CMB"] alone does not
systematically miss a Florida brewpub population.

## Active-only filter

Primary Status == "20" ("Current" per the readme.pdf status table) is applied.
Of 332 raw CMB rows in the 2026-08-30 fetch, 330 are Primary Status "20" and 2
are "21" ("Temp Cert" -- a temporary certificate while an application is
pending, not yet a full license); those 2 are dropped. Secondary Status is
NOT filtered on: of the 330 Primary-Status-"20" rows, 3 carry Secondary Status
"21" ("Litigation" -- a pending-litigation flag per readme.pdf, "License not
allowed to be transferred... " but not itself evidence the license is
inactive), which are kept. All 330 kept rows have Location State == "FL" and a
non-null future-or-current Expiration Date; no additional out-of-state or
expiration-date filtering was needed for this fetch (documented for
reproducibility against future re-fetches, same posture wi_dor.py takes).

## County name normalization

Florida DBPR's license extracts use an internal 2-digit numeric county code
(11-77 for its 67 counties; 78/99 = Unknown, 79 = Out of State, 80 = Foreign),
documented in DBPR's own readme.pdf ("COUNTY CODES AND NAMES FOR ALL
OCCUPATIONS" table) -- this is DBPR's own administrative county assignment,
used directly here rather than geocoding (same precedent as il_liquor.py /
pa_liquor.py / mi_lara.py). The readme's own spellings needed three fixes to
match TIGER/Census FL county names exactly (verified against the cached
TIGER county polygon layer): "DADE" -> "Miami-Dade" (renamed in 1997, long
after DBPR's internal code table was set), "DESOTA" -> "DeSoto", "HIGHLAND"
-> "Highlands". No rows in the 2026-08-30 CMB/Current fetch carry code 78/79/
80/99 (Unknown/Out-of-state/Foreign), so no rows are dropped by this step.
"""

from __future__ import annotations

import glob
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

RAW_DIR = Path("data/raw/fl_dbpr")
PUBLIC_RECORDS_PAGE_URL = "https://www2.myfloridalicense.com/alcoholic-beverages-and-tobacco/public-records/"
EXPORT_URL = "https://www2.myfloridalicense.com/sto/file_download/extracts/bd4005lic.csv"

BREWERY_SERIES = ["CMB"]

_COUNTY_CODE_TO_NAME = {
    "11": "Alachua", "12": "Baker", "13": "Bay", "14": "Bradford", "15": "Brevard",
    "16": "Broward", "17": "Calhoun", "18": "Charlotte", "19": "Citrus", "20": "Clay",
    "21": "Collier", "22": "Columbia", "23": "Miami-Dade", "24": "DeSoto", "25": "Dixie",
    "26": "Duval", "27": "Escambia", "28": "Flagler", "29": "Franklin", "30": "Gadsden",
    "31": "Gilchrist", "32": "Glades", "33": "Gulf", "34": "Hamilton", "35": "Hardee",
    "36": "Hendry", "37": "Hernando", "38": "Highlands", "39": "Hillsborough", "40": "Holmes",
    "41": "Indian River", "42": "Jackson", "43": "Jefferson", "44": "Lafayette", "45": "Lake",
    "46": "Lee", "47": "Leon", "48": "Levy", "49": "Liberty", "50": "Madison",
    "51": "Manatee", "52": "Marion", "53": "Martin", "54": "Monroe", "55": "Nassau",
    "56": "Okaloosa", "57": "Okeechobee", "58": "Orange", "59": "Osceola", "60": "Palm Beach",
    "61": "Pasco", "62": "Pinellas", "63": "Polk", "64": "Putnam", "65": "St. Johns",
    "66": "St. Lucie", "67": "Santa Rosa", "68": "Sarasota", "69": "Seminole", "70": "Sumter",
    "71": "Suwannee", "72": "Taylor", "73": "Union", "74": "Volusia", "75": "Wakulla",
    "76": "Walton", "77": "Washington",
}

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def fetch(force: bool = False) -> Path:
    """Download DBPR's weekly Alcoholic Beverage Manufacturers/Distributors export, or reuse cache."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "bd4005lic_*.csv")))
    if existing and not force:
        return Path(existing[-1])

    resp = requests.get(EXPORT_URL, headers=_HEADERS, timeout=120)
    resp.raise_for_status()
    if not resp.content.lstrip().startswith(b'"Board"'):
        raise RuntimeError(
            "Expected a CSV response (header starting '\"Board\",...') from DBPR's "
            "bd4005lic.csv extract; got something else -- the export's location or schema "
            "may have changed."
        )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"bd4005lic_{ts}.csv"
    dest.write_bytes(resp.content)

    df = pd.read_csv(dest, encoding="latin1", dtype=str)
    log_fetch(
        source="fl_dbpr", url=EXPORT_URL, dest_path=str(dest), row_count=len(df),
        notes="statewide DBPR/ABT profession-4005 (Alcoholic Beverage Manufacturers/"
              f"Distributors) weekly export, linked from the Public Records page "
              f"({PUBLIC_RECORDS_PAGE_URL})",
    )
    return dest


def load() -> pd.DataFrame:
    """Load the cached export, filter to active malt-beverage manufacturer premises.

    See module docstring for the full inclusion-rule reasoning.
    """
    path = fetch()
    df = pd.read_csv(path, encoding="latin1", dtype=str)
    n0 = len(df)

    brewery = df[df["Series"].isin(BREWERY_SERIES)].copy()
    log_filter("fl_dbpr", f"Series in {BREWERY_SERIES} (Manufacturer of Malt Beverages)", n0, len(brewery))

    n_before_active = len(brewery)
    active = brewery[brewery["Primary Status"] == "20"].copy()
    log_filter(
        "fl_dbpr", "Primary Status == '20' (Current)", n_before_active, len(active),
        notes="drops Primary Status '21' (Temp Cert -- pending application, not yet a full "
              "license); Secondary Status is not filtered on (a '21' Litigation flag does not "
              "mean the underlying license is inactive)",
    )

    n_before_state = len(active)
    in_state = active[active["Location State"] == "FL"].copy()
    log_filter("fl_dbpr", "Location State == 'FL'", n_before_state, len(in_state))

    n_before_county = len(in_state)
    in_state["county_name"] = in_state["Location County"].map(_COUNTY_CODE_TO_NAME)
    in_state = in_state[in_state["county_name"].notna()].copy()
    log_filter(
        "fl_dbpr", "Location County maps to a named FL county (drops Unknown/Out-of-State/Foreign)",
        n_before_county, len(in_state),
    )

    in_state["fl_dbpr_id"] = in_state["License Number"]
    in_state["state"] = "FL"
    in_state["lat"] = pd.NA
    in_state["lon"] = pd.NA
    in_state = in_state.rename(columns={
        "Owner Name": "owner_name",
        "DBA": "licensee_name",
        "Series": "license_type",
        "License Number": "license_number",
        "Location Address 1": "street_address",
        "Location City": "city",
        "Location ZIP": "zip",
        "Expiration Date": "expiration_date",
    })
    in_state["licensee_name"] = in_state["licensee_name"].fillna(in_state["owner_name"])

    return in_state[[
        "fl_dbpr_id", "license_number", "licensee_name", "license_type", "street_address",
        "city", "county_name", "state", "zip", "expiration_date", "lat", "lon",
    ]]


def county_counts() -> pd.DataFrame:
    """Convenience aggregate: active CMB (malt-beverage manufacturer) licenses per county."""
    df = load()
    counts = df.groupby("county_name").size().rename("fl_dbpr_count").reset_index()
    return counts
