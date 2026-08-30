"""Pennsylvania Liquor Control Board (PLCB) statewide license export — brewery
calibration source for a *control* state.

Source: https://www.plcbplus.pa.gov/pub/LicenseExport.aspx — a public,
no-login "CSV Download of All Licenses" link, surfaced directly on PLCB+'s own
public License Search page (https://www.plcbplus.pa.gov/pub/Default.aspx
?PossePresentation=LicenseSearch, titled "License Search / Data Export"),
which caps its interactive search UI at 200 rows and explicitly tells the user
to use this CSV link "to view more than 200 licenses." This is a genuine bulk
export -- one GET request returns all ~60,300 active/expired/pending/
safekeeping PLCB license *records* statewide (each renewal cycle of a license
gets its own row, so this is larger than the ~56,800 distinct license IDs)
across all ~80 license types, record level, no scraping of the interactive
form required.

CAVEAT -- this endpoint is slow (60-110s) and was observed returning
incomplete/truncated CSVs on some requests during development: three fetch
attempts against the live endpoint returned 40,522 / 57,598 / 60,358 total
rows respectively, and a fourth attempt failed to parse outright (cut off
mid-row). Spot-checking the smaller pulls showed they were strict subsets of
the larger ones (e.g. every one of the 40,522-row pull's active Brewery
records was also present in the 60,358-row pull, plus ~200 more, including
well-known operating breweries like Troegs) -- i.e. the short pulls look like
partial/truncated reads of the same underlying report, not different data.
`fetch()` below retries on a parse failure and on an implausibly small row
count, and always logs the row count actually retained so a future partial
pull is visible in the manifest rather than silently undercounting. All
figures quoted in this docstring are from a verified-complete 60,358-row pull
(cross-checked against two independent repeat fetches that landed on the
same 60,358 total).

Pennsylvania is a control state: the PLCB itself retails wine and spirits
(Fine Wine & Good Spirits stores), which is structurally different from every
other calibration state so far (all license-based, not control-based). But
manufacturing -- including brewing -- is still privately licensed, not run by
the state, so a brewery license list exists here just as it does in the
license-based states. Two structural quirks specific to PA surfaced while
building this module, both documented below.

## License-type landscape

The export's "License Type" column has no separate "Limited Brewery" category
today (PA historically distinguished "Brewery" from a smaller-volume "Limited
Brewery" license by statute, but the current PLCB+ system does not expose that
split -- checked the full ~80-type dropdown on the License Search page; only
"Brewery" appears, with production-volume tiers apparently handled by fee
schedule rather than a separate license type now). The brewery-adjacent types
actually present are: Brewery (590 active), Brewery Pub (30 active), Brewery
Storage (216 active), and Alternating Brewer (9 active) -- counts from the
verified-complete pull.

## Inclusion rule

INCLUDED (Status == "Active" only):
  - "Brewery" -- the primary production/manufacturer license.
  - "Brewery Pub" -- PLCB's on-premises brewpub retail privilege, PLCB's
    closest thing to a distinct brewpub license. In every one of the 30
    active cases, the same licensee also holds an active "Brewery" record at
    the identical premises (companion-license dedup below merges these), so
    in practice this type never contributes an independent location -- it's
    kept in the inclusion list for robustness in case a future refresh has a
    brewpub-only holder.
  - "Alcohol Beverage" WHERE the licensee or premises name contains "BREW"
    (case-insensitive) -- see "Alcohol Beverage" note below. Of the 5 records
    this matches (Lion Brewery x2 premises, Troegs, American Craft Brewery,
    Yards), 4 collapse into an already-counted "Brewery" record at the same
    premises in the dedup step; only one survives independently: Lion
    Brewery's second PA site at 1001 Sathers Dr, Pittston (Luzerne County) --
    which also holds an active Distillery and Brewery Storage license there,
    so it may be primarily a distilling/storage expansion rather than a
    second brewing line, an ambiguity this dataset can't resolve.

EXCLUDED:
  - "Brewery Storage" -- verified by inspection (e.g. Troegs' Brewery Storage
    sits in Lancaster County while its actual production site, licensed under
    "Brewery"/"Alcohol Beverage", is in Dauphin County) to be a secondary
    warehouse/storage license, not itself a brewing location.
  - "Alternating Brewer" -- PA's "alternating proprietorship" arrangement,
    where one licensee brews on another licensee's already-licensed premises.
    Checked all 9 active records: Duquesne Brewing Company, Stoneys Brewing
    Company, and American Craft Brewery each hold an active Alternating
    Brewer license at 100 33rd St, Latrobe -- the same physical address
    already counted once under CBC Latrobe Acquisition LLC's own active
    "Brewery" license. Including this type would count one shared
    contract-brewing facility as 4 separate breweries; excluding it means a
    handful of legitimate but site-less "gypsy brewer" arrangements (e.g. Cape
    May Brewing's PA-licensed contract site in Philadelphia) are not
    separately counted. This mirrors the project's general preference for
    physical locations over licensed legal entities.
  - "Wholesale Liquor Manufacturer" (4 records, all Expired, none brewery
    names) -- checked and excluded on inspection.
  - Rows with County == "Out of State" -- PLCB licenses some out-of-state
    manufacturers; none currently appear among the included types, but the
    filter is kept defensively (mirrors GA DOR's exclusion of out-of-state
    BREWERY/BREWPUB licensees, which *did* need it).

## The "Alcohol Beverage" consolidation

PLCB appears to be mid-rollout on a new, consolidated "Alcohol Beverage"
manufacturer license (56 active records, all first issued Nov 2024 or later --
consistent with the "ra-lblicensingmod@pa.gov" licensing-modernization contact
address on the PLCB+ login page) that a licensee can hold *in addition to* a
pre-existing category-specific license (Brewery, Distillery, Winery) --
confirmed for Lion, Troegs, Yards, and American Craft Brewery, all of which
hold both an active "Brewery" and an active "Alcohol Beverage" record at the
same address. Most of the 56 active "Alcohol Beverage" records are
distilleries and wineries with no brewery counterpart at all (e.g. Robert
Mazza -- Mazza Vineyards, a well-known Erie winery; multiple
"*Distilling*"/"*Spirits*" LLCs). The name-contains-"BREW" filter used here is
a heuristic, not an exhaustive classification: any brewery that holds only an
"Alcohol Beverage" record under a name that doesn't contain "brew" (e.g. a
geographic or founder's name, with no separate "Brewery" record to catch it)
would be silently missed by this filter and is not otherwise recoverable from
this dataset alone. This is the single biggest completeness caveat for the PA
count -- flagged rather than resolved, per this project's rule against
inventing a fix a data source can't support.

## Companion-license dedup

PLCB issues a brewery's on-premises retail privilege ("Brewery Pub", or for a
few recently-migrated licensees, a second "Alcohol Beverage" record) as a
*second* license record at the *same physical premises* as its "Brewery"
production license -- confirmed for Tired Hands, Victory, Yards, Dock Street
South, Arundel Cellars, Lion, Troegs, American Craft Brewery, and others
(identical premises address, identical licensee). Left undeduplicated this
would double-count roughly 34 physical locations (590 Brewery + 30 Brewery
Pub + 5 brewery-named Alcohol Beverage = 625 raw rows, vs. 591 after dedup).
Records are deduplicated on (licensee name, premises street number, premises
ZIP5), keeping the "Brewery" record over its "Brewery Pub"/"Alcohol Beverage"
companion when both exist at the same address. This key is loose enough to
match minor address-string variants (e.g. "121 N Market St" vs. "121 North
Market St") while still keeping genuinely separate production sites owned by
the same company distinct (e.g. Victory Brewing's Parkesburg and Downingtown
facilities, and Lion Brewery's Wilkes-Barre/Pittston/Quakertown sites, are
NOT merged with each other, since their street numbers/ZIPs differ). Net
result: 591 active brewery-relevant PLCB locations (590 "Brewery" + 1
"Alcohol Beverage" -- Lion's Pittston site, per above).

## County, not lat/lon

Unlike CO/OR's Socrata data, this export has no lat/lon -- but it does carry
a "County" column directly (PLCB's own administrative assignment, all 67 PA
counties plus "Out of State" observed), which is more reliable than deriving
county from a geocoded point. No Census Geocoder fallback is used here; see
mi_lara.py for the project's precedent of using a source's own county field
directly rather than geocoding when one is already provided. The one
formatting fix applied is PLCB's "Mckean County" -> "McKean County" (a
casing quirk specific to this one county; every other county name in the
export already matches TIGER's proper-case spelling).
"""

from __future__ import annotations

import glob
import io
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

RAW_DIR = Path("data/raw/pa_plcb")
LICENSE_SEARCH_PAGE_URL = "https://www.plcbplus.pa.gov/pub/Default.aspx?PossePresentation=LicenseSearch"
EXPORT_URL = "https://www.plcbplus.pa.gov/pub/LicenseExport.aspx"

PRIMARY_BREWERY_TYPES = ["Brewery", "Brewery Pub"]
ALCOHOL_BEVERAGE_TYPE = "Alcohol Beverage"
_LICENSE_TYPE_PRIORITY = {"Brewery": 0, "Brewery Pub": 1, "Alcohol Beverage": 2}

_COUNTY_NAME_FIXES = {"Mckean": "McKean"}

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

# The smallest of the three complete/independent pulls observed during development
# was 60,358 rows (two repeat fetches landed on exactly that number); the two
# confirmed-truncated pulls were 40,522 and 57,598 rows. This threshold sits
# between the largest known-truncated pull and the smallest known-complete one --
# see the module docstring CAVEAT for the full story.
_MIN_PLAUSIBLE_ROWS = 58_000
_MAX_FETCH_ATTEMPTS = 4


def fetch(force: bool = False) -> Path:
    """Download the full PLCB active-license CSV export, or reuse the cache.

    Retries on a parse failure or an implausibly small row count: this
    endpoint has been observed returning incomplete/truncated CSVs on some
    requests (see the module docstring CAVEAT). Keeps the largest
    successfully-parsed response across attempts, on the empirically-verified
    assumption that a shorter pull is a truncated subset of a longer one, not
    different data.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "plcb_license_export_*.csv")))
    if existing and not force:
        return Path(existing[-1])

    best_df: pd.DataFrame | None = None
    best_text: str | None = None
    attempts_log: list[str] = []

    for attempt in range(1, _MAX_FETCH_ATTEMPTS + 1):
        resp = requests.get(EXPORT_URL, headers=_HEADERS, timeout=180)
        resp.raise_for_status()
        if not resp.text.lstrip().startswith("LID,"):
            raise RuntimeError(
                "Expected a CSV response (header starting 'LID,...') from PLCB LicenseExport.aspx; "
                "got something else -- the export endpoint or its schema may have changed."
            )

        try:
            df = pd.read_csv(io.StringIO(resp.text))
        except pd.errors.ParserError as exc:
            attempts_log.append(f"attempt {attempt}: parse error ({exc})")
            continue

        attempts_log.append(f"attempt {attempt}: {len(df)} rows")
        if best_df is None or len(df) > len(best_df):
            best_df, best_text = df, resp.text
        if len(df) >= _MIN_PLAUSIBLE_ROWS:
            break

    if best_df is None or best_text is None:
        raise RuntimeError(
            f"All {_MAX_FETCH_ATTEMPTS} attempts to fetch PLCB LicenseExport.aspx failed to parse "
            f"as CSV. Attempts: {attempts_log}"
        )
    if len(best_df) < _MIN_PLAUSIBLE_ROWS:
        # Don't silently ship a likely-truncated pull -- surface it and let the caller decide.
        raise RuntimeError(
            f"PLCB LicenseExport.aspx returned only {len(best_df)} rows across "
            f"{_MAX_FETCH_ATTEMPTS} attempts, below the {_MIN_PLAUSIBLE_ROWS}-row plausibility "
            f"floor for a complete export (see module docstring CAVEAT). Attempts: {attempts_log}"
        )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"plcb_license_export_{ts}.csv"
    dest.write_text(best_text)

    log_fetch(
        source="pa_liquor", url=EXPORT_URL, dest_path=str(dest), row_count=len(best_df),
        notes="full statewide export of ALL PLCB license types/statuses, public no-login bulk "
              f"CSV linked from the License Search page ({LICENSE_SEARCH_PAGE_URL}); "
              f"fetch attempts={attempts_log} -- this endpoint sometimes returns a truncated "
              "partial read, so multiple attempts were made and the largest successfully-parsed "
              "response was kept (see module docstring CAVEAT)",
    )
    return dest


def _house_number(addr: object) -> str:
    m = re.match(r"\s*(\d+)", str(addr))
    return m.group(1) if m else ""


def _zip5(addr: object) -> str:
    m = re.search(r"(\d{5})(?:-\d{4})?\s*$", str(addr))
    return m.group(1) if m else ""


def load() -> pd.DataFrame:
    """Load the cached export, filter to active brewery-relevant licenses, and
    dedup companion licenses at the same premises. See module docstring for
    the full inclusion-rule reasoning.
    """
    path = fetch()
    df = pd.read_csv(path)
    n0 = len(df)

    active = df[df["Status"] == "Active"]
    log_filter("pa_liquor", "Status == 'Active'", n0, len(active))

    primary = active[active["License Type"].isin(PRIMARY_BREWERY_TYPES)]
    log_filter("pa_liquor", f"License Type in {PRIMARY_BREWERY_TYPES}", len(active), len(primary))

    alc_bev = active[active["License Type"] == ALCOHOL_BEVERAGE_TYPE]
    is_brewery_name = (
        alc_bev["Licensee"].str.contains("BREW", case=False, na=False)
        | alc_bev["Premises"].str.contains("BREW", case=False, na=False)
    )
    alc_bev_brew = alc_bev[is_brewery_name]
    log_filter(
        "pa_liquor", "License Type == 'Alcohol Beverage' AND (Licensee or Premises) contains 'BREW'",
        len(alc_bev), len(alc_bev_brew),
        notes="recovers breweries (Lion, Troegs, American Craft Brewery, Yards) migrated into "
              "PLCB's post-Nov-2024 consolidated manufacturer license; name-based heuristic, "
              "not exhaustive -- see module docstring 'Alcohol Beverage consolidation' section",
    )

    combined = pd.concat([primary, alc_bev_brew], ignore_index=True)
    n_before_dedup = len(combined)

    combined["_prio"] = combined["License Type"].map(_LICENSE_TYPE_PRIORITY)
    combined["_house_num"] = combined["Premises Address"].apply(_house_number)
    combined["_zip5"] = combined["Premises Address"].apply(_zip5)
    combined["_dedup_key"] = (
        combined["Licensee"].str.upper().str.strip() + "|" + combined["_house_num"] + "|" + combined["_zip5"]
    )
    combined = combined.sort_values("_prio").drop_duplicates("_dedup_key", keep="first")
    log_filter(
        "pa_liquor",
        "dedup companion Brewery Pub / Alcohol Beverage license at the same premises as an "
        "already-counted Brewery license (same licensee, street number, ZIP5)",
        n_before_dedup, len(combined),
        notes="PLCB issues the on-premises retail privilege as a second license record at a "
              "brewery's own production site -- same physical location, second license row",
    )

    n_before_oos = len(combined)
    combined = combined[combined["County"] != "Out of State"]
    log_filter(
        "pa_liquor", "County != 'Out of State'", n_before_oos, len(combined),
        notes="defensive filter; 0 rows dropped as of this fetch, but mirrors GA DOR's "
              "out-of-state-manufacturer exclusion in case a future export includes one",
    )

    n_before_county = len(combined)
    combined = combined[combined["County"].notna()]
    log_filter("pa_liquor", "County not null", n_before_county, len(combined))

    combined = combined.reset_index(drop=True)
    combined["county_name"] = (
        combined["County"].str.replace(" County", "", regex=False).replace(_COUNTY_NAME_FIXES)
    )

    out = combined.rename(columns={
        "LID": "pa_liquor_id",
        "License Number": "license_number",
        "License Type": "license_type",
        "Licensee": "licensee_name",
        "Premises": "premises_name",
        "Premises Address": "street_address",
        "Municipality": "municipality",
        "Last Issue Date": "last_issue_date",
        "Expiration Date": "expiration_date",
    })
    out["state"] = "PA"
    out["lat"] = pd.NA
    out["lon"] = pd.NA

    return out[[
        "pa_liquor_id", "license_number", "licensee_name", "premises_name", "license_type",
        "street_address", "municipality", "county_name", "state", "last_issue_date",
        "expiration_date", "lat", "lon",
    ]]


def county_counts() -> pd.DataFrame:
    """Convenience aggregate: active brewery-relevant PLCB licenses per county."""
    df = load()
    counts = df.groupby("county_name").size().rename("pa_liquor_count").reset_index()
    return counts
