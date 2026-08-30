"""Texas Alcoholic Beverage Commission (TABC) licenses, via the state's Socrata open-data API.

Source: https://data.texas.gov/resource/kguh-7q9z.json (dataset "TABCLicenses",
id kguh-7q9z, on the Texas Open Data Portal / data.texas.gov, which runs on
Socrata like Colorado's and Oregon's portals). A genuine bulk open-data API —
record level, one row per active primary license, with a `txcounty` field
already attached (no geocoding needed for county-level rollups). This is the
*primary*-license table only (TABC's own description: "twenty eight (28)
primary license types"); optional subordinate authorizations that attach to a
primary license are not broken out as separate rows.

License-type judgment call: TABC's Sept. 1, 2021 license consolidation merged
the old "Brewer's Permit (B)" and "Manufacturer's License (BA)" into a single
"Brewer's License (BW)" -- the production/manufacturing license for malt
beverages (see TABC's consolidation chart,
https://www.tabc.texas.gov/static/sites/default/files/2021-03/tabc-sept-2021-license-consolidation-explained-chart.pdf).
BW is therefore the modern analog of the "manufacturer's license" this
project's other calibration states use as the brewery-relevant type, and is
what BREWERY_LICENSE_TYPES selects.

The Brewpub License (BP) is, per that same chart, an "optional subordinate
authority" attached to a retail permit (Mixed Beverage Permit MB, Wine and
Malt Beverage Retailer's Permit BG, or Retail Dealer's On-Premise License BE)
-- it is NOT one of the 28 primary license types and does not appear as its
own aimslicensetype value in this table (confirmed empirically: no BP rows,
and legacylicenseclass == 'BP' returns zero rows). There is no separate public
open-data table exposing subordinate authorizations. This means brewpub-style
locations that hold only a retail permit + BP subordinate authority (no
separate BW) are NOT captured here -- a real, documented gap, not a silent
drop. It parallels the CBP brewpub/NAICS-722511 undercount already noted in
cbp.py, and is the reason this module's capture rate versus Brewers
Association comes in lower than the other four calibration states.
"""

from __future__ import annotations

import glob
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

RAW_DIR = Path("data/raw/tx_liquor")
SODA_URL = "https://data.texas.gov/resource/kguh-7q9z.json"

# "Brewer's License (BW)" -- the post-2021-consolidation production/manufacturing
# license for malt beverages (merged from the legacy Brewer's Permit (B) and
# Manufacturer's License (BA)). See module docstring for why BP (brewpub) is
# not included: it is a subordinate authority, not a primary license type, and
# is not exposed as a distinct row in this dataset.
BREWERY_LICENSE_TYPES = ["BW"]


def fetch(force: bool = False) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "tx_breweries_*.json")))
    if existing and not force:
        return Path(existing[-1])

    where_clause = "aimslicensetype in (" + ",".join(f"'{t}'" for t in BREWERY_LICENSE_TYPES) + ")"
    resp = requests.get(SODA_URL, params={"$where": where_clause, "$limit": 5000}, timeout=60)
    resp.raise_for_status()
    records = resp.json()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"tx_breweries_{ts}.json"
    dest.write_text(json.dumps(records))

    log_fetch(source="tx_liquor", url=SODA_URL, dest_path=str(dest), row_count=len(records),
              notes=f"license_types={BREWERY_LICENSE_TYPES}")
    return dest


def load() -> pd.DataFrame:
    path = fetch()
    records = json.loads(path.read_text())
    n0 = len(records)

    rows = []
    for r in records:
        rows.append({
            "licensee_name": r.get("aimsownername"),
            "doing_business_as": r.get("aimstradename"),
            "license_id": r.get("aimslicenseid"),
            "license_type": r.get("aimslicensetype"),
            "legacy_license_class": r.get("legacylicenseclass"),
            "street_address": r.get("locationaddress"),
            "city": r.get("city"),
            "zip": r.get("zip"),
            "county": r.get("txcounty"),
        })
    df = pd.DataFrame(rows)

    has_county = df["county"].notna()
    df = df[has_county].reset_index(drop=True)
    log_filter("tx_liquor", "has txcounty", n0, len(df))

    return df


def county_counts() -> pd.DataFrame:
    df = load()
    n0 = len(df)
    # groupby() silently drops rows whose key is NaN, so verify the aggregate
    # accounts for every row rather than trusting that silently.
    counts = df.groupby("county").size().rename("liquor_count").reset_index()
    n_after = int(counts["liquor_count"].sum())
    if n_after != n0:
        log_filter("tx_liquor", "groupby county (rows with missing county dropped)", n0, n_after)
    counts.columns = ["county_name", "liquor_count"]
    return counts
