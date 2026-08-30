"""Illinois Liquor Control Commission (ILCC) — statewide daily license export.

Source: https://ilcc.illinois.gov/content/dam/soi/en/web/ilcc/datasources/ilcc-licenses-daily-export.csv
This CSV is linked directly from ILCC's own FOIA Section 4 disclosure page
(https://ilcc.illinois.gov/divisions/legal/freedom-of-information-act-requests.html,
"ilcc-licenses-daily-export.csv") -- a genuine bulk, no-login, statewide export
of every license record ILCC has issued, refreshed daily. It is NOT the
interactive "License Lookup" search tool (ilcc.illinois.gov/resources/liquor-license-lookup.html)
or the new Salesforce-based ILCC Portal (ilccportal.illinois.gov), neither of
which offers a bulk download -- both were checked and ruled out before this
file was found. data.illinois.gov (Illinois's Socrata open-data portal) was
also checked directly (domain-scoped catalog query) and carries zero
liquor/alcohol/ILCC datasets.

## Columns and scope

One row per license record: license_class, license_number, acct_name, county,
cust_name, business_type, current_effective_date, current_issue_date,
current_expiration_date, ibt, renewal, acct_city, acct_state, dba_address
(a single "STREET  CITY STATE, ZIPPLUS4" string), owners. There is no active/
inactive status column -- but there IS a genuine "county" column, ILCC's own
administrative assignment, used directly here rather than geocoding (same
precedent as pa_liquor.py and mi_lara.py: a source's own county field beats a
geocoded point when both are available).

## Not active-only, despite being a "daily export"

Critical finding from inspecting the file: 47.8% of ALL ~33,200 rows statewide
(not just brewery rows) carry a current_expiration_date already in the past
relative to the fetch date. This is a cumulative license roll, not a
current-license-only feed. A current_expiration_date >= today filter is
therefore applied and is NOT defensive/optional the way WI's or PA's
equivalent checks are -- it removes just over half of raw brewery-class rows
(254 of 499) as of the 2026-08-30 fetch. This plays the same role as PA
PLCB's Status == "Active" filter and MI LARA's Status == "Active" filter;
IL's export just expresses it via expiration date instead of a status field.

## License classes and inclusion rule

The 2026-08-30 fetch's license_class values relevant to a physical brewing
operation, per ILCC's brewer licensing scheme (235 ILCS 5/5-1 et seq.):

- "3C - BREWER" -- the base manufacturer's license every Illinois brewery
  holds.
- "3Y - CLASS 1 BREWER" -- production up to 930,000 gal/yr, self-distribution
  up to 232,500 gal/yr; an overlay ON TOP OF the base "3C - BREWER" license at
  the same premises for most, but not all, holders (see dedup below).
- "3Z - CLASS 2 BREWER" -- production up to 3,720,000 gal/yr.
- "7Y - CLASS 3 BREWER" -- the newest/smallest craft tier.
- "1C - BREW PUB" -- a *retail*-side license (in the 1-series, alongside
  ordinary on-premises consumption licenses) held by brewpubs that brew beer
  for on-site sale at a restaurant/tavern. Checked for premises-address
  overlap against the four manufacturer classes above: 0 of 115 in-state 1C
  premises collide with a manufacturer-class premises (one exception --
  see dedup below) -- brewpubs here are a genuinely separate physical-location
  population from standalone production breweries, not a second license on
  the same site (unlike PA's "Brewery Pub" pattern), so 1C is unioned in
  rather than treated as a companion/duplicate license.

BREWERY_LICENSE_CLASSES is the union of all five. Excluded, deliberately: 3-series
non-resident/importing-distributor classes (3I, 3J -- out-of-state brand
registration, no IL brewing), 2-series distributor classes, and all
retailer/caterer/special-event classes other than 1C.

## Dedup: same brewery, two license classes at the same premises

A single physical brewery frequently holds BOTH "3C - BREWER" and one of the
Class 1/2/3 overlay licenses at the same address (e.g. Analytical Brewing's
Peoria and Lexington locations each carry a 3C-BREWER and a 7Y-CLASS-3-BREWER
license with identical dba_address). Left undeduplicated, this roughly
doubles the manufacturer-class row count relative to physical locations (374
in-state active manufacturer-class rows -> 247 distinct premises). Records
are deduplicated on (acct_name upper/stripped, house number parsed from
dba_address, ZIP5) -- the same dedup key shape pa_liquor.py uses (licensee +
house number + ZIP5), which correctly keeps genuinely distinct businesses
that share a building separate: "NOON WHISTLE BREWING COMPANY" (1C Brew Pub)
and "NEURONOVA BREWING" (3C/7Y manufacturer) share one Lombard street address
+ ZIP (different suite) under different account names, and an address-only
dedup key would have wrongly merged them; the acct_name-qualified key keeps
them as two locations, which is almost certainly correct (two brewers
co-located in one building, not one brewer under two names). Within a
dedup group, the row is kept in this priority order: 3C > Class 1 > Class 2
> Class 3, arbitrary among true duplicates since only one row per group is
kept and downstream columns don't distinguish it further.

## Out-of-state manufacturer licensees

10 of the 384 raw in-state-scoped manufacturer-class rows carry
acct_state in {WI, IA, MI} or county == "OUT OF STATE" (e.g. Haymarket Beer
Company's Bridgman, MI location, and Pilot Project Brewing's Milwaukee, WI
location, both IL-licensed to ship into the state). Dropped via the same
out-of-state-manufacturer judgment call GA DOR and WI DOR made: an IL
manufacturer's license held by a brewery with no physical Illinois brewing
location is not an Illinois brewery.

## County name normalization

ILCC's county column is upper-cased with no diacritics/capitalization
(e.g. "DUPAGE", "MCHENRY", "LA SALLE", "ST. CLAIR", "DEWITT"). Title-casing
alone gets all but seven of Illinois's 102 counties right; the remaining
seven get an explicit fix so they match TIGER/Census naming exactly for the
downstream county_name merge key (verified against the Census API's own IL
county list): DeKalb, De Witt, DuPage, LaSalle, McDonough, McHenry, McLean.
"Jo Daviess" and "St. Clair" and "Rock Island" all come out correctly from
plain .title() case and need no fix.
"""

from __future__ import annotations

import glob
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

RAW_DIR = Path("data/raw/il_liquor")
FOIA_PAGE_URL = "https://ilcc.illinois.gov/divisions/legal/freedom-of-information-act-requests.html"
EXPORT_URL = "https://ilcc.illinois.gov/content/dam/soi/en/web/ilcc/datasources/ilcc-licenses-daily-export.csv"

MANUFACTURER_CLASSES = [
    "3C - BREWER",
    "3Y - CLASS 1 BREWER",
    "3Z - CLASS 2 BREWER",
    "7Y - CLASS 3 BREWER",
]
BREWPUB_CLASSES = ["1C - BREW PUB"]
BREWERY_LICENSE_CLASSES = MANUFACTURER_CLASSES + BREWPUB_CLASSES

_MFG_PRIORITY = {c: i for i, c in enumerate(MANUFACTURER_CLASSES)}

_ADDR_RE = re.compile(
    r"^(?P<street>.+)\s{2,}(?P<city>[A-Za-z .'\-]+)\s+(?P<state>[A-Z]{2}),\s*(?P<zip9>\d+)\s*$"
)

_COUNTY_NAME_FIXES = {
    "DEKALB": "DeKalb",
    "DEWITT": "De Witt",
    "DUPAGE": "DuPage",
    "MCDONOUGH": "McDonough",
    "MCHENRY": "McHenry",
    "MCLEAN": "McLean",
    "LA SALLE": "LaSalle",
}

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def fetch(force: bool = False) -> Path:
    """Download ILCC's daily statewide license-export CSV, or reuse the cache."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "ilcc_licenses_daily_export_*.csv")))
    if existing and not force:
        return Path(existing[-1])

    resp = requests.get(EXPORT_URL, headers=_HEADERS, timeout=120)
    resp.raise_for_status()
    if not resp.text.lstrip().startswith("license_class,"):
        raise RuntimeError(
            "Expected a CSV response (header starting 'license_class,...') from ILCC's daily "
            "license export; got something else -- the export's location or schema may have changed."
        )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"ilcc_licenses_daily_export_{ts}.csv"
    dest.write_text(resp.text)

    df = pd.read_csv(dest)
    log_fetch(
        source="il_liquor", url=EXPORT_URL, dest_path=str(dest), row_count=len(df),
        notes="full statewide ILCC license roll, ALL license classes/eras, linked from the "
              f"FOIA Section 4 disclosure page ({FOIA_PAGE_URL}); this is a cumulative export, "
              "not active-only -- see module docstring",
    )
    return dest


def _parse_address(addr: object) -> tuple[str | None, str | None, str | None, str | None]:
    if not isinstance(addr, str):
        return None, None, None, None
    m = _ADDR_RE.match(addr.strip())
    if not m:
        return None, None, None, None
    return m.group("street"), m.group("city").title(), m.group("state"), m.group("zip9")[:5]


def _normalize_county(raw: str) -> str:
    raw = raw.strip().upper()
    return _COUNTY_NAME_FIXES.get(raw, raw.title())


def load() -> pd.DataFrame:
    """Load the cached export, filter to active brewery-relevant IL premises, and dedup
    companion manufacturer-class licenses at the same premises. See module docstring for
    the full inclusion-rule reasoning.
    """
    path = fetch()
    df = pd.read_csv(path, dtype=str)
    n0 = len(df)

    brewery = df[df["license_class"].isin(BREWERY_LICENSE_CLASSES)].copy()
    log_filter("il_liquor", f"license_class in {BREWERY_LICENSE_CLASSES}", n0, len(brewery))

    n_before_active = len(brewery)
    exp_date = pd.to_datetime(brewery["current_expiration_date"], format="%m/%d/%Y", errors="coerce")
    today = pd.Timestamp.now(tz=None).normalize()
    active = brewery[exp_date >= today].copy()
    log_filter(
        "il_liquor", "current_expiration_date >= today (not-yet-expired; export is cumulative, "
        "not active-only -- see module docstring)",
        n_before_active, len(active),
    )

    n_before_state = len(active)
    in_state = active[
        (active["acct_state"] == "IL") & ~active["county"].isin(["", "OUT OF STATE"]) & active["county"].notna()
    ].copy()
    log_filter(
        "il_liquor", "acct_state == 'IL' and county not in ('', 'OUT OF STATE')",
        n_before_state, len(in_state),
        notes="drops out-of-state manufacturer licensees (e.g. Haymarket Beer Co.'s Bridgman, MI "
              "site, Pilot Project Brewing's Milwaukee, WI site) that hold an IL manufacturer's "
              "license to ship into the state but have no physical IL brewing location -- same "
              "judgment call GA DOR and WI DOR made",
    )

    parsed = in_state["dba_address"].apply(
        lambda a: pd.Series(_parse_address(a), index=["street_address", "city", "addr_state", "zip"])
    )
    in_state = pd.concat([in_state.reset_index(drop=True), parsed.reset_index(drop=True)], axis=1)

    n_before_parse = len(in_state)
    in_state = in_state[in_state["street_address"].notna()].copy()
    log_filter("il_liquor", "dba_address parsed successfully", n_before_parse, len(in_state))

    def house_number(street: str) -> str:
        m = re.match(r"\s*(\S+)", str(street))
        return m.group(1).upper() if m else ""

    in_state["_house_num"] = in_state["street_address"].apply(house_number)
    in_state["_dedup_key"] = (
        in_state["acct_name"].str.upper().str.strip() + "|" + in_state["_house_num"] + "|" + in_state["zip"]
    )

    mfg = in_state[in_state["license_class"].isin(MANUFACTURER_CLASSES)].copy()
    brewpub = in_state[in_state["license_class"].isin(BREWPUB_CLASSES)].copy()

    n_before_dedup = len(mfg)
    mfg["_prio"] = mfg["license_class"].map(_MFG_PRIORITY)
    mfg = mfg.sort_values("_prio").drop_duplicates("_dedup_key", keep="first")
    log_filter(
        "il_liquor",
        "dedup companion manufacturer-class license (3C base + Class 1/2/3 overlay) at the same "
        "premises (acct_name + house number + ZIP5)",
        n_before_dedup, len(mfg),
        notes="ILCC issues the production-volume overlay license (3Y/3Z/7Y) as a second license "
              "record at a brewery's own 3C-BREWER premises -- same physical location, second "
              "license row",
    )

    combined = pd.concat([mfg, brewpub], ignore_index=True)
    combined["county_name"] = combined["county"].apply(_normalize_county)
    combined["il_liquor_id"] = combined["license_number"]
    combined["state"] = "IL"
    combined["lat"] = pd.NA
    combined["lon"] = pd.NA
    combined = combined.rename(columns={
        "acct_name": "licensee_name",
        "license_class": "license_type",
        "license_number": "license_number",
        "current_expiration_date": "expiration_date",
    })

    return combined[[
        "il_liquor_id", "license_number", "licensee_name", "license_type", "street_address",
        "city", "county_name", "state", "zip", "expiration_date", "lat", "lon",
    ]]


def county_counts() -> pd.DataFrame:
    """Convenience aggregate: active brewery-relevant ILCC licenses per county."""
    df = load()
    counts = df.groupby("county_name").size().rename("il_liquor_count").reset_index()
    return counts
