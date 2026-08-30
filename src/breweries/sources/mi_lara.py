"""Michigan LARA (Liquor Control Commission) Master License List — brewery calibration source.

Source: https://www.michigan.gov/lara/bureau-list/lcc/licensing-list — a genuine
bulk-downloadable, weekly-updated Excel export of every active/conditional/
escrowed liquor license in Michigan, including business name, address, and
license type. Unlike NC ABC's individual-permit search, this is record-level
(not just aggregate counts), so it supports both state/county rollup checks
and OBDB record matching.

Brewery manufacturing licenses are Group == "Manufacturer" with
Type in {"Micro Brewer", "Brewer"} (the second is used for the handful of
larger production breweries, e.g. Bell's, Founders).
"""

from __future__ import annotations

import glob
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

RAW_DIR = Path("data/raw/mi_lara")
MASTER_LIST_URL = (
    "https://www.michigan.gov/lara/-/media/Project/Websites/lara/lcc/License-Lists/Master-License-List.xlsx"
)
BREWERY_TYPES = {"Micro Brewer", "Brewer"}


def fetch(force: bool = False) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "master_license_list_*.xlsx")))
    if existing and not force:
        return Path(existing[-1])

    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    resp = requests.get(MASTER_LIST_URL, headers=headers, timeout=120)
    resp.raise_for_status()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"master_license_list_{ts}.xlsx"
    dest.write_bytes(resp.content)

    df = pd.read_excel(dest, header=1)
    log_fetch(source="mi_lara", url=MASTER_LIST_URL, dest_path=str(dest), row_count=len(df),
              notes="full statewide license list, all license types")
    return dest


def load_breweries() -> pd.DataFrame:
    """Load the full license list and filter to active brewery manufacturing licenses."""
    path = fetch()
    df = pd.read_excel(path, header=1)
    n0 = len(df)

    active = df[df["Status"] == "Active"]
    log_filter("mi_lara", "Status == 'Active'", n0, len(active))

    manufacturer = active[active["Group"] == "Manufacturer"]
    log_filter("mi_lara", "Group == 'Manufacturer'", len(active), len(manufacturer))

    breweries = manufacturer[manufacturer["Type"].isin(BREWERY_TYPES)]
    log_filter("mi_lara", f"Type in {sorted(BREWERY_TYPES)}", len(manufacturer), len(breweries))

    return breweries.reset_index(drop=True)


# LARA's county field uses abbreviations/spacing that don't match ACS's Census county
# names (e.g. LARA's "GR TRAVERSE" is ACS's "Grand Traverse"). Mapped by inspection.
COUNTY_NAME_FIXES = {
    "Gr Traverse": "Grand Traverse",
    "St Clair": "St. Clair",
    "St Joseph": "St. Joseph",
}


def county_counts() -> pd.DataFrame:
    df = load_breweries()
    counts = df.groupby("County: County").size().rename("lara_permit_count").reset_index()
    counts.columns = ["county", "lara_permit_count"]
    counts["county"] = counts["county"].str.title().replace(COUNTY_NAME_FIXES)
    return counts
