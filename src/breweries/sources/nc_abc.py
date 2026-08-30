"""North Carolina ABC Commission brewery permit counts, by county.

Source: https://abc2.nc.gov/Search/PermitCounts — the ABC Commission's public
"Permit Counts" report generator. This is a legitimate public-records report
export (POST returns a generated .xlsx), not a bulk open-data portal.

Note: the interactive record-level permit *search* (abc2.nc.gov/Search/Permit)
sits behind Cloudflare bot management and returned 500s to a scripted session;
we did not attempt to work around that, since evading bot protection isn't
something this pipeline should do, and it wouldn't be reproducible anyway.
This report endpoint is the closest thing NC offers to bulk ABC data — it
gives county-level AE (Brewery manufacturing permit) counts, not a per-business
list, so it is used only for state/county rollup calibration, not deduplication
or record-level matching.
"""

from __future__ import annotations

import glob
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch

COUNTS_URL = "https://abc2.nc.gov/Search/SubmitPermitsCount"
PERMIT_PAGE_URL = "https://abc2.nc.gov/Search/PermitCounts"
RAW_DIR = Path("data/raw/nc_abc")

# Permit type code for "AE - Brewery" (manufacturing permit) in the ABC Commission's form.
BREWERY_PERMIT_TYPE_ID = "5"


def fetch(force: bool = False) -> Path:
    """Request the county-level AE-Brewery permit count report, or reuse the cached copy."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "brewery_permit_counts_*.xlsx")))
    if existing and not force:
        return Path(existing[-1])

    session = requests.Session()
    session.get(PERMIT_PAGE_URL, timeout=30)  # establishes cookies
    resp = session.post(
        COUNTS_URL,
        data={"PermitSearchPermitTypes": BREWERY_PERMIT_TYPE_ID},
        headers={"Referer": PERMIT_PAGE_URL},
        timeout=30,
    )
    resp.raise_for_status()
    if not resp.content.startswith(b"PK"):
        raise RuntimeError("Expected an .xlsx response from NC ABC PermitCounts; got something else.")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"brewery_permit_counts_{ts}.xlsx"
    dest.write_bytes(resp.content)

    df = pd.read_excel(dest)
    log_fetch(source="nc_abc", url=COUNTS_URL, dest_path=str(dest), row_count=len(df),
              notes="county-level AE-Brewery permit counts, not a record-level list")
    return dest


def load_county_counts() -> pd.DataFrame:
    """Load county-level AE-Brewery permit counts, dropping the trailing 'Totals' row."""
    path = fetch()
    df = pd.read_excel(path)
    df.columns = ["county", "brewery_permit_count"]
    df = df[df["county"] != "Totals"].reset_index(drop=True)
    df["county"] = df["county"].str.replace(" County", "", regex=False).str.strip()
    return df
