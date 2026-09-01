"""Nebraska Liquor Control Commission (NLCC) — statewide "Active License Roster" export.

Source: the NLCC's own "Active License Roster" page
(https://lcc.nebraska.gov/licensing-sdl/active-license-roster) links directly to a
dated Excel file (e.g. "Active Roster 2026-08-31.xlsx", refreshed regularly at a
`/sites/default/files/licensing/Active%20Licensing/` path) -- a genuine bulk,
no-login, statewide export of every active NLCC license. This is NOT the
interactive `nebraska.gov/nlcc/license_search/licsearch.cgi` per-record search
tool or the POSSE case-management portal (posse-lcc.nebraska.gov), both linked
from the same page but neither offering a bulk download -- checked and ruled
out before this file was found. Nebraska's open-data presence
(nebraska.gov/government/open-data/) was also checked and carries no liquor/
alcohol dataset; the NLCC's own roster is the only bulk source.

## Columns and scope

One row per license record: Troop Area, License Type Group, Class, Secondary
License type, License Number, License State (a status field, e.g. "Active"),
Trade Name, Address (a single "STREET_x000D_\\nCITY, STATE ZIP+4" string),
City, County, County Index Number, Corporate Limits Designation, Phone Number,
Manager Name, Licensee, Corporate Address, Monthly Issue/Effective/Expiration
Date, Legal Description. There is a genuine "County" column (NLCC's own
administrative assignment), used directly here rather than geocoding -- same
precedent as il_liquor.py / pa_liquor.py / mi_lara.py.

## License class and inclusion rule

Nebraska licenses breweries under a single dedicated class: **Class L, "Craft
Brewery License"** (Neb. Rev. Stat. 53-123.14 & 53-171; annual fee $250 per the
NLCC's own "Craft Brewery Information & Guidelines" brochure). A Nebraska
craft brewery license permits production up to 20,000 barrels/yr with retail
sale/self-distribution rights bundled in -- there is no separate NLCC
"Manufacturer" license-type group for beer (the roster's `License Type Group`
column only ever takes the values Retail / Shipper / Miscellaneous; Class L
brewery licenses are filed under the "Retail" group, since Nebraska issues the
craft brewery license "in lieu of" a manufacturer's license, per statute).
BREWERY_LICENSE_CLASS = "L". Nebraska's statute (53-103.03/53-103.44) taxes
hard cider as beer, so a handful of Class L holders are cideries or
winery+cidery combos (e.g. "Vala's Orchard Cider Co", "Saro Cider", "Glacial
Till Vineyard & Winery") -- these are kept in, not name-filtered out, because
Class L is itself Nebraska's own regulatory definition of a beer/cider
manufacturer, the same "follow the state's own classification, not our
judgment about the business name" principle IL's 1C Brew Pub inclusion and
WI's broad "Brewery" permit sweep-in both follow. A separate Class Y ("Farm
Winery") license exists for wine-only production and is excluded (not in
scope here) -- Mac's Creek Winery & Brewery, which holds both a Y and an L
license at the same premises, is only counted once via its L row.

Excluded, deliberately: Class T ("Shipper") rows for well-known out-of-state
craft breweries (Pabst, Summit, Odell, New Belgium, Camo) shipping into
Nebraska under NLCC's direct-shipping statute -- these are not Nebraska
breweries, the same out-of-state-shipper judgment call IL, GA, and WI made
for their own out-of-state manufacturer/shipper rows.

## No cumulative-roster duplication for Class L specifically

Unlike Class C (ordinary retail liquor), which the same "Active Roster" export
carries with two overlapping rows per license number when a license renewal
period has already been issued ahead of the current license year -- e.g.
"Dick's Place" appears twice, covering 2025-2026 and 2026-2027 license years
-- Class L rows show no such duplication as of the 2026-08-31 fetch: 66 raw
Class L rows equal 66 unique license numbers and 66 unique (Trade Name,
Address) pairs. No expiration-date "active-only" filter is therefore needed
for Class L (the roster's own name -- "Active License Roster" -- and its
one-license-number-per-brewery reality both hold here), unlike IL's cumulative
ILCC export.

## Multi-location licensees

"Brickway Brewing & Distilling" holds two separate Class L licenses at two
distinct Nebraska addresses (Omaha and La Vista) under two different
corporate names (Borgata Brewing and Distilling; Nebraska Beverage Partners
LLC) -- kept as two rows/two locations, a legitimate multi-site brewer, not a
duplicate (same principle as WA's and IL's multi-location chains).
"""

from __future__ import annotations

import glob
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

RAW_DIR = Path("data/raw/ne_liquor")
ROSTER_PAGE_URL = "https://lcc.nebraska.gov/licensing-sdl/active-license-roster"
BREWERY_CLASS = "L"

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
_LINK_RE = re.compile(r'href="([^"]*Active%20Roster[^"]*\.xlsx)"', re.IGNORECASE)


def _discover_roster_url() -> str:
    resp = requests.get(ROSTER_PAGE_URL, headers=_HEADERS, timeout=60)
    resp.raise_for_status()
    m = _LINK_RE.search(resp.text)
    if not m:
        raise RuntimeError(
            "Could not find an 'Active Roster ....xlsx' download link on the NLCC "
            f"roster page ({ROSTER_PAGE_URL}) -- the page's layout may have changed."
        )
    href = m.group(1)
    if href.startswith("http"):
        return href
    return "https://lcc.nebraska.gov" + href


def fetch(force: bool = False) -> Path:
    """Download the NLCC's Active License Roster Excel export, or reuse the cache."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "ne_active_roster_*.xlsx")))
    if existing and not force:
        return Path(existing[-1])

    url = _discover_roster_url()
    resp = requests.get(url, headers=_HEADERS, timeout=120)
    resp.raise_for_status()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"ne_active_roster_{ts}.xlsx"
    dest.write_bytes(resp.content)

    df = _read_raw(dest)
    log_fetch(
        source="ne_liquor", url=url, dest_path=str(dest), row_count=len(df),
        notes=f"full statewide NLCC active license roster, all license classes, linked from "
              f"the NLCC's own roster page ({ROSTER_PAGE_URL})",
    )
    return dest


def _read_raw(path: Path) -> pd.DataFrame:
    """The roster's real header row sits a few blank rows into the sheet; locate it
    by searching for the known 'Troop Area' header cell rather than assuming a fixed
    offset, since NLCC's own template has shifted before.
    """
    raw = pd.read_excel(path, header=None)
    hdr_matches = raw.index[raw.apply(lambda r: r.astype(str).str.contains("Troop Area").any(), axis=1)]
    if len(hdr_matches) == 0:
        raise RuntimeError(f"Could not locate the 'Troop Area' header row in {path}")
    return pd.read_excel(path, header=int(hdr_matches[0]))


def load() -> pd.DataFrame:
    """Load the cached roster and filter to active Class L (Craft Brewery) licenses.
    See module docstring for the full inclusion-rule reasoning.
    """
    path = fetch()
    df = _read_raw(path)
    n0 = len(df)

    brewery = df[df["Class"] == BREWERY_CLASS].copy()
    log_filter("ne_liquor", f"Class == '{BREWERY_CLASS}' (Craft Brewery License)", n0, len(brewery))

    n_before_dedup = len(brewery)
    brewery = brewery.drop_duplicates(subset=["License Number"])
    log_filter(
        "ne_liquor", "dedup on License Number (defensive; no duplicates found as of this fetch, "
        "see module docstring)", n_before_dedup, len(brewery),
    )

    brewery["ne_liquor_id"] = brewery["License Number"]
    brewery["licensee_name"] = brewery["Trade Name"]
    brewery["street_address"] = brewery["Address"].astype(str).str.split(r"[\r\n]+").str[0].str.strip()
    brewery["county_name"] = brewery["County"].astype(str).str.strip()
    brewery["city"] = brewery["City"]
    brewery["state"] = "NE"
    brewery["expiration_date"] = brewery["Monthly Current Expiration Date"]
    brewery["lat"] = pd.NA
    brewery["lon"] = pd.NA

    return brewery[[
        "ne_liquor_id", "License Number", "licensee_name", "Class", "street_address",
        "city", "county_name", "state", "expiration_date", "lat", "lon",
    ]].rename(columns={"License Number": "license_number", "Class": "license_type"})


def county_counts() -> pd.DataFrame:
    """Convenience aggregate: active Class L (Craft Brewery) licenses per county."""
    df = load()
    counts = df.groupby("county_name").size().rename("ne_liquor_count").reset_index()
    return counts
