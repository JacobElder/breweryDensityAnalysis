"""Connecticut Department of Consumer Protection (DCP), Liquor Control Division —
statewide "Liquor Permits" dataset on Connecticut's open-data portal.

Source: https://data.ct.gov/resource/gwv2-eswx.json (dataset "Liquor Permits",
id gwv2-eswx, on data.ct.gov, which runs on Socrata like Texas's, Colorado's,
and Oregon's portals -- found by checking data.ct.gov directly for
alcohol/liquor datasets, same method used for the other Socrata-portal
calibration states). A genuine bulk, no-login, record-level open-data API,
one row per permit record (not per business), refreshed on an ongoing basis.
This is NOT DCP's "eLicense" interactive licensee lookup
(elicense.ct.gov/lookup), which was checked and ruled out (single-record
search only, no bulk export) before this dataset was found.

## Permit-class ("credential") codes and inclusion rule

This dataset has no documented public data dictionary for its `credential`
prefix codes (checked: neither the dataset's own Socrata metadata/column
descriptions, nor DCP's Liquor Control web pages, publish one). The correct
code was identified empirically by querying the live dataset for every
`dba`/`backer` containing "BREW" and cross-referencing well-known, currently-
operating physical Connecticut breweries (Two Roads Brewing Co. of Stratford,
New Park Brewing, Black Hog Brewing, Connecticut Valley Brewing Co.) against
their live `status`:

- **"LMB" = Manufacturer Permit for Beer** -- the credential prefix these
  known-active physical CT breweries hold (verified: `status == "ACTIVE"`
  for e.g. Two Roads Brewing Co., New Park Brewing, Black Hog Brewing). 112
  distinct active LMB permits as of the 2026-08-30 fetch -- a plausible count
  for Connecticut's craft brewing sector, and the prefix this module filters
  to. Some LMB rows are alternating proprietorships sharing one physical
  facility under different business names (e.g. Peak Organic Brewing Co. LLC,
  Lawson's Finest Liquids, and City Steam Brewery all list the same 1700
  Stratford Ave, Stratford premises, each under a `.AP` credential suffix and
  a distinct legal entity) -- these are kept as separate rows, same as
  il_liquor.py's precedent of keeping genuinely distinct co-located
  businesses separate rather than merging on address alone; it does not
  affect county-level totals either way since all rows at one address fall in
  the same county.
- **"LBD" rejected**: also appears heavily on brewery-named `dba`/`backer`
  rows (Oskar Blues, Cigar City, Sapporo, Founders, Southern Tier, Mikkeller
  San Diego, etc.) but these are all large national/out-of-state brewers with
  no Connecticut brewing location -- LBD is Connecticut's Certificate of
  Approval / out-of-state brand registration class, not a CT manufacturer
  permit (same role as GA DOR's and WI DOR's out-of-state-shipper exclusion,
  just expressed as a wholly separate credential prefix here rather than a
  filterable field on a shared class).
- **"LCT" rejected**: superficially brewery-adjacent (some historical/inactive
  rows are named e.g. "Bumski's Brew & Links," "City Steam Brewery"), but a
  direct query of every currently-ACTIVE LCT record shows they are all
  catering businesses (Z Catering, Becker's Catering, Abigail Kirsch
  Connecticut, etc.) -- LCT is Connecticut's Caterer's Liquor Permit class.
- **"LBP" rejected**: this looks like it should be Connecticut's historical
  Brew Pub permit class (e.g. "John Harvard's Brew Pub," "Hartford Brew
  House," "Willimantic Brewing Co" all appear under LBP) but zero LBP rows
  are currently ACTIVE -- the class appears to have been retired/folded into
  LMB (e.g. "Elicit Brewing Co" holds inactive LBP rows plus current active
  LMB rows in different towns as the business expanded). No distinct
  currently-active Connecticut brewpub class was found; brewpub-style
  Connecticut breweries appear to hold the same LMB Manufacturer Permit as
  standalone breweries.

BREWERY_CREDENTIAL_PREFIX = "LMB." is therefore the sole inclusion filter.

## Active-only filter and dedup

`status == "ACTIVE"` is applied server-side via a SoQL `$where` clause (this
dataset carries decades of historical/inactive permit records, so filtering
at fetch time keeps the cached raw file scoped to what this module actually
uses). Multiple raw rows can share one `credential` (one row per co-permittee
individual on a multi-owner LLC, e.g. "The New Cambridge Project" has two
listed individual permittees on identical premises/dates) -- deduplicated on
the base credential (the part before any "." suffix, so ".AP"
alternating-proprietorship rows are NOT collapsed into each other, only true
same-permit co-permittee duplicates are), keeping the first row per group.

## No county field -- geocoded like wi_dor.py / ga_dor.py

This dataset supplies a premises address (`permit_address`/`permit_city`/
`permit_state`/`permit_zip`) but no county field, so county assignment goes
through breweries.geocode.fill_missing_coords (Census Geocoder) +
assign_geographies, same mechanism as every other liquor source in this
project that lacks its own county column. A small number of rows (2 of 112
active LMB permits in the 2026-08-30 fetch) have a null `permit_address`;
these fall back to the permittee's `backer_address`/`backer_city`/
`backer_state`/`backer_zip` (the business's registered mailing address)
rather than being dropped, on the same reasoning wi_dor.py applies to rows
with missing Business Address.

## Connecticut counties are Census "planning regions," not the traditional 8 counties

Since the 2022 vintage, the Census Bureau's TIGER/Line county-equivalent
layer, and its ACS/CBP county-level tables, represent Connecticut using its
9 Councils-of-Governments planning regions (e.g. "Capitol," "Greater
Bridgeport," "Naugatuck Valley") rather than the state's traditional 8
counties (Fairfield, Hartford, etc.), which no longer function as
governmental units in CT. This project's TIGER/ACS/CBP loaders already pull
from the Census API and the current TIGER vintage, so they already reflect
planning regions automatically -- this module needs no special handling,
but the resulting `county_name` values for Connecticut (e.g. "Capitol"
instead of "Hartford") are a real, expected difference from every other
state in this project and are called out here so they aren't mistaken for a
data-quality bug.
"""

from __future__ import annotations

import glob
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

RAW_DIR = Path("data/raw/ct_dcp")
SODA_URL = "https://data.ct.gov/resource/gwv2-eswx.json"

BREWERY_CREDENTIAL_PREFIX = "LMB."


def fetch(force: bool = False) -> Path:
    """Query CT's Socrata Liquor Permits dataset for active Manufacturer Permits for
    Beer, server-side filtered, or reuse the cache."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "ct_lmb_active_*.json")))
    if existing and not force:
        return Path(existing[-1])

    where_clause = f"credential like '{BREWERY_CREDENTIAL_PREFIX}%' AND status = 'ACTIVE'"
    resp = requests.get(SODA_URL, params={"$where": where_clause, "$limit": 5000}, timeout=60)
    resp.raise_for_status()
    records = resp.json()
    if len(records) >= 5000:
        raise RuntimeError(
            "CT Liquor Permits query returned >= 5000 rows for a credential-prefix filter "
            "that historically returns ~100-150 -- the $limit may be truncating results; "
            "raise it before trusting this fetch."
        )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"ct_lmb_active_{ts}.json"
    dest.write_text(json.dumps(records))

    log_fetch(
        source="ct_dcp", url=SODA_URL, dest_path=str(dest), row_count=len(records),
        notes=f"credential like '{BREWERY_CREDENTIAL_PREFIX}%' AND status='ACTIVE', "
              "server-side $where filter against data.ct.gov's Liquor Permits Socrata dataset",
    )
    return dest


def load() -> pd.DataFrame:
    """Load the cached export and dedup co-permittee rows sharing one credential.

    Returns one row per active LMB (Manufacturer Permit for Beer) with
    street/city/state/zip and empty lat/lon columns for the Census Geocoder
    fallback -- no coordinates or county are supplied directly by this source.
    See module docstring for the full inclusion-rule reasoning.
    """
    path = fetch()
    records = json.loads(path.read_text())
    n0 = len(records)

    rows = []
    for r in records:
        street = r.get("permit_address") or r.get("backer_address")
        city = r.get("permit_city") or r.get("backer_city")
        state = r.get("permit_state") or r.get("backer_state")
        zip5 = (r.get("permit_zip") or r.get("backer_zip") or "")[:5]
        credential = r.get("credential") or ""
        # Base credential = prefix + permit number, e.g. "LMB.0001527.AP" -> "LMB.0001527".
        # Preserves ".AP" (alternating proprietorship) as a distinct permit rather than
        # collapsing it into whatever LMB number happens to precede it alphabetically.
        cred_parts = credential.split(".")
        credential_base = ".".join(cred_parts[:2]) if len(cred_parts) >= 2 else credential
        rows.append({
            "credential": credential,
            "credential_base": credential_base,
            "licensee_name": r.get("dba") or r.get("backer"),
            "backer": r.get("backer"),
            "effective_date": r.get("effective_date"),
            "expire_date": r.get("expire_date"),
            "street_address": street,
            "city": city,
            "state": state,
            "zip": zip5,
        })
    df = pd.DataFrame(rows)

    n_before_dedup = len(df)
    df = df.drop_duplicates("credential_base", keep="first").copy()
    log_filter(
        "ct_dcp", "dedup co-permittee rows sharing one credential (base credential, keep first)",
        n_before_dedup, len(df),
        notes="multiple raw rows can list separate individual co-permittees for one LLC-held "
              "permit at one premises; '.AP' alternating-proprietorship suffixes are preserved "
              "so genuinely distinct co-located businesses are NOT merged",
    )

    df = df.reset_index(drop=True)
    df["ct_dcp_id"] = df["credential_base"]
    df["license_type"] = "LMB (Manufacturer Permit for Beer)"
    df["lat"] = pd.NA
    df["lon"] = pd.NA

    return df[[
        "ct_dcp_id", "credential", "licensee_name", "license_type", "street_address",
        "city", "state", "zip", "effective_date", "expire_date", "lat", "lon",
    ]]
