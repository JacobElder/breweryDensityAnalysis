"""Virginia ABC (Alcoholic Beverage Control Authority) statewide licensee
export -- brewery calibration source for Virginia, a second *control* state
(after PA) in this project's calibration set.

Source: the export linked directly from Virginia ABC's own public, no-login
"Find a License" search page (https://www.abc.virginia.gov/licenses/find-a-license),
whose "Download the full licensee file" link points at a bulk Excel export
hosted on Virginia ABC's own Azure blob storage:
https://salicenseeexport.blob.core.windows.net/export/LicenseSearchReport.xlsx

This is a genuine bulk export, not a scrape of the interactive search UI --
one GET request returns the entire statewide licensee/permit/application
roster (66,883 rows as fetched, every license/permit/application type ABC
issues: retail, banquet, industry, shipper, tobacco, etc.), refreshed daily
(observed `Last-Modified` timestamp matched the morning of the fetch date).
The workbook has three blank/title rows before the header ("Run As of
<timestamp>" in row 2); `fetch()`/`load()` locate the header row by content
(first row whose first cell is "LICENSE #") rather than a hardcoded row
index, since that offset is not guaranteed stable across refreshes.

Virginia is a control state (Virginia ABC directly retails spirits through
its own stores), structurally like Pennsylvania -- but exactly as with PA's
PLCB, manufacturing (including brewing) is privately licensed, not run by
the state, so a genuine brewery license roster exists here too.

## License-type / establishment-type landscape

Every industry brewing license appears under RECORD TYPE == "Industry Brewery
License" (477 rows) or "Industry Brewery Application" (18 rows, not yet
issued -- excluded). Within "Industry Brewery License", the ESTABLISHMENT
TYPE column carries Virginia's own answer to the manufacturer-license split
the project looks for in every state:

  - "Brewery" (387 rows) -- the general/standard brewing manufacturer
    license, tiered by the CAPACITY DETAILS column ("Up to 500", "501-10,000",
    "Over 10,000" barrels/year), no farm requirement.
  - "Limited Brewery" (108 rows) -- Virginia's farm-brewery license: capped at
    15,000 barrels/year, requires the brewery to be located on a farm in the
    Commonwealth and to use some farm-grown ingredients in production
    (Va. Code Title 4.1 farm-brewery provisions; confirmed via Virginia ABC's
    own license-definition pages and cross-checked against secondary sources,
    since the definition pages themselves are a JS-rendered SPA that could
    not be fetched as static text).

Both are physical, currently-operating brewing locations -- the farm
requirement is a *siting* constraint, not a sign the license is non-physical
or inactive -- so both are included, mirroring CO's inclusion of both
"Manufacturer (brewery)" and "Brew Pub" types as jointly exhaustive of
"physical brewing location" for that state.

Excluded ESTABLISHMENT TYPE values that also contain the substring "brew":
"Gourmet Brewing Shop" (a home-brew-supplies retail shop, not a manufacturer)
and a long tail of one-off named special events ("Brewfest", "Oktobrewfest",
"Home Brew Club Meeting", etc. -- banquet/special-event permits, not
manufacturer licenses). None of these carry RECORD TYPE == "Industry Brewery
License", so they are excluded automatically by the RECORD TYPE filter and
never reach the ESTABLISHMENT TYPE check.

## Status filtering

Industry brewery rows carry their active/expired state in "BEER/WINE STATUS"
(the underlying beer manufacturing privilege), not a generic "Status" column
-- Active (416), Inactive (61, expired/closed -- excluded), Pending (0 for
License rows; all 18 Pending rows are the separately-excluded Application
record type). Filtered to BEER/WINE STATUS == "Active".

## County, not lat/lon -- and why that matters more here than elsewhere

Like PA's PLCB export, this file carries the licensed premises' county
directly in a "COUNTY" column, so no geocoding is needed. That directness is
more than a convenience in Virginia: it independent cities (Richmond,
Roanoke, Franklin, Fairfax, Alexandria, ...) are their own county-equivalent
alongside a *same-named* county (e.g. Richmond city vs. Richmond County,
Roanoke city vs. Roanoke County) -- Census TIGER's bare `NAME` field for
these is identical for the city and the county ("Richmond", "Roanoke", ...),
so a spatial-join-derived county assignment reduced to bare name would
silently merge the two. ABC's own COUNTY column instead already carries the
"City"/"County" suffix (e.g. "Richmond City" vs. "Richmond County",
"Roanoke City" vs. "Roanoke County"), which is exactly the disambiguator
needed. Checked all 416 active brewery-relevant rows against the full set of
133 Virginia TIGER county-equivalent names (NAMELSAD, case-insensitive):
415/416 matched directly; the sole mismatch is documented below. The
downstream build script (scripts/build_va_county_dataset.py) uses this
COUNTY column as the merge key for the liquor source and resolves OBDB/OSM
(which only carry lat/lon) to the same county-equivalent via TIGER's
GEOID rather than its bare NAME, specifically to avoid this collision --
see that script's docstring for the full mechanism.

One legacy value: a single row (Beale's Beer, Bedford) carries COUNTY ==
"Bedford City" -- Bedford City reverted from independent-city to town status
and was absorbed into Bedford County effective July 1, 2013 (a well-known,
one-off case in Virginia's list of county-equivalents); current TIGER data
has no separate "Bedford city" polygon. Remapped to "Bedford County" here so
it joins to a real TIGER geography instead of silently becoming an unmatched
row.

One source data-entry error, corrected: Solstice Farm Brewery (6565
Blacksburg Rd, Catawba -- an unincorporated community entirely within
Roanoke *County*, nowhere near the independent city of Roanoke) carries
COUNTY == "Roanoke City" on its current active license (013814864, issued
2026-06-15). Its own immediately-prior license record at the identical
address (013218280, now Inactive, superseded by the current one) correctly
carries COUNTY == "Roanoke County" -- i.e. VA ABC's own prior record, plus
the address itself, both contradict the current record's county field.
Corrected to "Roanoke County" by license number rather than silently trusted
or silently dropped, since Roanoke is one of this state's explicit
face-validity check regions and a wrong city/county assignment there would
be exactly the kind of error this project's checkpoints exist to catch.

## Export-glitch dedup

Deduplicated on (premises street address, ZIP5), keeping the first row per
premises -- not on license number or (name, address), for a documented
reason: this snapshot contains one exact case where a single license number
(013838893) was emitted as all 4 combinations of two unrelated business names
("Chimney Ridge Vineyards LLC", a real Botetourt County farm winery/brewery
near Buchanan, VA, and "Crooked Run Brewery Taproom", a real, unrelated
Loudoun-area brewery with an Alexandria address) crossed with both of their
addresses -- an evident name*address cross-join bug in this one day's export,
not two brands sharing one physical site. Deduping by license number would
have arbitrarily kept only one of these two real, distinct breweries;
deduping by (name, address) would have kept all 4 fabricated combinations.
Deduping by address alone correctly collapses this to the 2 real premises
(one row per distinct street address, whichever of the two crossed names
happened to sort first for that address -- so the name attached to one of
these two rows may be swapped from the correct one, a caveat this dataset
can't resolve further). The same address-based key also collapses one
ordinary case of the same premises being re-issued a new license number
(St George Brewing Company / St George Brewing Co, 204 Challenger Way,
Hampton, license numbers 013828401 and 13584). Net effect: 416 -> 413 rows.
"""

from __future__ import annotations

import glob
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

RAW_DIR = Path("data/raw/va_abc")
FIND_A_LICENSE_PAGE_URL = "https://www.abc.virginia.gov/licenses/find-a-license"
EXPORT_URL = "https://salicenseeexport.blob.core.windows.net/export/LicenseSearchReport.xlsx"

BREWERY_RECORD_TYPE = "Industry Brewery License"
BREWERY_ESTABLISHMENT_TYPES = ["Brewery", "Limited Brewery"]

_COUNTY_FIXES = {"bedford city": "Bedford County"}

# Source data-entry error, verified against the premises' own prior (now-inactive)
# license record and against the address itself -- see module docstring.
_LICENSE_COUNTY_FIXES = {"013814864": "Roanoke County"}

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def fetch(force: bool = False) -> Path:
    """Download the VA ABC statewide licensee export, or reuse the cache."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "va_licensee_export_*.xlsx")))
    if existing and not force:
        return Path(existing[-1])

    resp = requests.get(EXPORT_URL, headers=_HEADERS, timeout=180)
    resp.raise_for_status()
    if not resp.content.startswith(b"PK"):
        raise RuntimeError(
            "Expected an .xlsx response from the VA ABC LicenseSearchReport export; got something "
            "else -- the export endpoint may have changed or moved."
        )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"va_licensee_export_{ts}.xlsx"
    dest.write_bytes(resp.content)

    df = _read_with_header(dest)
    log_fetch(
        source="va_abc", url=EXPORT_URL, dest_path=str(dest), row_count=len(df),
        notes="full statewide export of ALL VA ABC license/permit/application types, public "
              f"no-login bulk XLSX linked from the Find a License page ({FIND_A_LICENSE_PAGE_URL})",
    )
    return dest


def _read_with_header(path: Path) -> pd.DataFrame:
    """Read the export, locating the header row by content (a fixed row index
    is not guaranteed stable across the daily-refreshed export)."""
    probe = pd.read_excel(path, header=None, nrows=20)
    header_rows = probe.index[probe[0] == "LICENSE #"]
    if len(header_rows) == 0:
        raise RuntimeError(
            "Could not locate the 'LICENSE #' header row in the first 20 rows of the VA ABC "
            "export -- the file layout may have changed."
        )
    header_row = int(header_rows[0])
    df = pd.read_excel(path, header=header_row)
    return df.loc[:, ~df.columns.str.contains("^Unnamed", regex=True)]


def _zip5(z: object) -> str:
    m = re.match(r"\s*(\d{5})", str(z))
    return m.group(1) if m else ""


def load() -> pd.DataFrame:
    """Load the cached export, filter to active brewery-relevant licenses, and
    dedup an export-glitch duplicate at the premises-address level. See module
    docstring for the full inclusion-rule and dedup reasoning.
    """
    path = fetch()
    df = _read_with_header(path)
    n0 = len(df)

    industry = df[df["RECORD TYPE"] == BREWERY_RECORD_TYPE]
    log_filter("va_abc", f"RECORD TYPE == {BREWERY_RECORD_TYPE!r}", n0, len(industry),
               notes="drops 'Industry Brewery Application' rows (not yet issued)")

    brewery = industry[industry["ESTABLISHMENT TYPE"].isin(BREWERY_ESTABLISHMENT_TYPES)]
    log_filter("va_abc", f"ESTABLISHMENT TYPE in {BREWERY_ESTABLISHMENT_TYPES}",
               len(industry), len(brewery),
               notes="excludes 'Gourmet Brewing Shop' (home-brew supply retail, not a "
                     "manufacturer) and named special-event/banquet permits containing 'brew'")

    n_before_status = len(brewery)
    active = brewery[brewery["BEER/WINE STATUS"] == "Active"].copy()
    log_filter("va_abc", "BEER/WINE STATUS == 'Active'", n_before_status, len(active),
               notes="drops Inactive (expired/closed) brewery licenses")

    active["county_name"] = (
        active["COUNTY"].astype(str).str.strip().str.lower().replace(_COUNTY_FIXES)
    )
    # Anything not remapped by the fix table above keeps its original (Title Case)
    # text -- only the one known legacy value needs correcting.
    is_fixed = active["COUNTY"].astype(str).str.strip().str.lower().isin(_COUNTY_FIXES)
    active.loc[~is_fixed, "county_name"] = active.loc[~is_fixed, "COUNTY"].astype(str).str.strip()

    n_license_fix = active["LICENSE #"].isin(_LICENSE_COUNTY_FIXES).sum()
    for lic, county in _LICENSE_COUNTY_FIXES.items():
        active.loc[active["LICENSE #"] == lic, "county_name"] = county
    if n_license_fix:
        log_filter("va_abc", "correct known source data-entry error in COUNTY by license number",
                   len(active), len(active),
                   notes=f"corrected {_LICENSE_COUNTY_FIXES} on {int(n_license_fix)} row(s); "
                         "see module docstring (Solstice Farm Brewery / Roanoke County)")

    n_before_dedup = len(active)
    active["_zip5"] = active["ZIP"].apply(_zip5)
    active["_addr_key"] = active["ADDRESS"].astype(str).str.upper().str.strip() + "|" + active["_zip5"]
    active = active.drop_duplicates("_addr_key", keep="first")
    log_filter(
        "va_abc", "dedup export-glitch/re-licensed duplicate at the same premises address",
        n_before_dedup, len(active),
        notes="collapses a name*address cross-join glitch under one shared license number "
              "(Chimney Ridge Vineyards / Crooked Run Brewery Taproom) and one ordinary "
              "re-licensing at the same premises (St George Brewing) -- see module docstring",
    )

    active = active.reset_index(drop=True)
    active["va_abc_id"] = active.index

    out = active.rename(columns={
        "LICENSE #": "license_number",
        "COMPANY / APPLICANT NAME": "licensee_name",
        "FACILITY OR ESTABLISHMENT NAME": "facility_name",
        "ESTABLISHMENT TYPE": "establishment_type",
        "CAPACITY DETAILS": "capacity_tier",
        "ADDRESS": "street_address",
        "CITY": "city",
        "ZIP": "zip",
        "ISSUED DATE": "issued_date",
        "REGION": "region",
    })
    out["state"] = "VA"
    out["lat"] = pd.NA
    out["lon"] = pd.NA

    return out[[
        "va_abc_id", "license_number", "licensee_name", "facility_name", "establishment_type",
        "capacity_tier", "street_address", "city", "county_name", "zip", "state",
        "issued_date", "region", "lat", "lon",
    ]]


def county_counts() -> pd.DataFrame:
    """Convenience aggregate: active brewery-relevant VA ABC licenses per county-equivalent."""
    df = load()
    counts = df.groupby("county_name").size().rename("va_abc_count").reset_index()
    return counts
