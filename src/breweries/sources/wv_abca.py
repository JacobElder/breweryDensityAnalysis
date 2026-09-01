"""West Virginia Alcohol Beverage Control Administration (ABCA) -- "West Virginia
Resident Brewers" list PDF.

Source: https://abca.wv.gov/media/32647/download?inline= (filename
"WV-NonIntoxicating-Beer-Distributor-and-Resident-Brewer-List.pdf"), linked directly
from ABCA's own "Forms and Applications" page
(https://abca.wv.gov/about/forms-and-applications) under the heading "Nonintoxicating
Beer Distributor and West Virginia Resident Brewer Lists" -- a genuine bulk, no-login
PDF combining two rosters: the state's licensed beer *distributors* (pages 1-3) and,
starting on page 4 under its own "WEST VIRGINIA RESIDENT BREWERS" header, the
statewide list of licensed resident brewers with physical address and county. This
module parses only the resident-brewer section. It is not the interactive
wvabca.com "License Search" tool (which was checked directly and confirmed to require
narrowing search criteria, not a bulk export) and is not the application-form PDF
found first at abca.wv.gov/media/31456 (that document is a Retail Class A license
application packet, not a roster, and was ruled out on inspection).

## Data-quality caveat: the list is a dated snapshot, not a live feed

The PDF's own last line reads "LAST UPDATED: July 2025" -- about 13 months stale
relative to this pipeline's 2026-08-31 fetch, even though the file's HTTP
`last-modified` header (2026-04-24) shows ABCA re-published the same underlying
July-2025 snapshot more recently without refreshing its contents. Unlike WY's
same-day-refreshed Wholesaler List or IL's daily export, this source should be
treated as carrying up to roughly a year of lag on brewery openings/closures --
noted here the same way IL's cumulative-vs-active-only finding and WI's/TX's lag
findings are noted for their sources.

## County field and inclusion rule

Each entry carries the format `Location: <County> County, WV` -- ABCA's own county
assignment, used directly (same precedent as il_liquor.py/wy_liquor.py: a source's own
county field beats geocoding). 34 entries are listed under "WEST VIRGINIA RESIDENT
BREWERS"; one, "WHIM (Neighborhood Kombuchery)" (Morgantown, Monongalia County), is a
kombucha producer rather than a beer/malt-beverage brewery and is dropped, consistent
with this project's standing convention of excluding cider/mead/non-beer fermented
producers (OBDB's own CIDERY_TYPES exclusion in obdb.py) -- WV licenses hard-kombucha
makers under the same Resident Brewer statute because kombucha can exceed 0.5% ABV,
but it is not a brewery by this pipeline's definition. The remaining 33 rows are kept
as-is; every one names a real, individually verifiable West Virginia brewery/brewpub
(e.g. Bridge Brew Works, Greenbrier Valley Brewing, Parkersburg Brewing Company,
Mountain State Brewing), so no further per-row classification is needed the way WY's
combined wholesaler/distributor list required.
"""

from __future__ import annotations

import glob
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from pypdf import PdfReader

from breweries.manifest import log_fetch, log_filter

RAW_DIR = Path("data/raw/wv_abca")
FORMS_PAGE_URL = "https://abca.wv.gov/about/forms-and-applications"
LIST_URL = "https://abca.wv.gov/media/32647/download?inline="

SECTION_HEADER = "WEST VIRGINIA RESIDENT BREWERS"
NON_BREWERY_NAMES = {"WHIM (Neighborhood Kombuchery)"}

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def fetch(force: bool = False) -> Path:
    """Download the WV ABCA Nonintoxicating Beer Distributor and Resident Brewer
    List PDF, or reuse the cached copy."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "wv_resident_brewer_list_*.pdf")))
    if existing and not force:
        return Path(existing[-1])

    resp = requests.get(LIST_URL, headers=_HEADERS, timeout=60)
    resp.raise_for_status()
    if resp.headers.get("content-type", "").split(";")[0] != "application/pdf":
        raise RuntimeError(
            f"Expected a PDF response from {LIST_URL}; got "
            f"content-type={resp.headers.get('content-type')!r} -- the document link "
            "may have changed."
        )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"wv_resident_brewer_list_{ts}.pdf"
    dest.write_bytes(resp.content)

    log_fetch(
        source="wv_abca", url=LIST_URL, dest_path=str(dest),
        notes=f"WV ABCA Nonintoxicating Beer Distributor and Resident Brewer List "
              f"PDF, linked from {FORMS_PAGE_URL}; last-modified header dated "
              f"{resp.headers.get('last-modified')}, but the document's own footer "
              "reads 'LAST UPDATED: July 2025' -- see module docstring staleness caveat",
    )
    return dest


def _parse_pdf(path: Path) -> pd.DataFrame:
    reader = PdfReader(str(path))
    pages_text = [page.extract_text() for page in reader.pages]

    # Isolate the "WEST VIRGINIA RESIDENT BREWERS" section: find the first page whose
    # text contains the section header and parse from there to the end of the document
    # (the distributor list precedes it and uses the same "Location: ... County, WV"
    # format, so the header is the only reliable section boundary).
    start_idx = next(
        (i for i, t in enumerate(pages_text) if SECTION_HEADER in t), None
    )
    if start_idx is None:
        raise RuntimeError(
            f"Could not find the {SECTION_HEADER!r} section header in the fetched PDF "
            "-- the document's layout may have changed."
        )
    text = "\n".join(pages_text[start_idx:])
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    rows = []
    for j, line in enumerate(lines):
        m = re.match(r"^Location:\s*(.+?)\s+County,\s*WV\.?$", line)
        if m:
            rows.append({
                "brewer_name": lines[j - 5],
                "address_line": lines[j - 4],
                "city_state_zip": lines[j - 3],
                "county_name": m.group(1).strip(),
            })
    return pd.DataFrame(rows)


def load() -> pd.DataFrame:
    """Load the cached PDF, parse the Resident Brewers section, and drop the one
    non-beer (kombucha) entry documented in the module docstring."""
    path = fetch()
    df = _parse_pdf(path)
    n0 = len(df)

    unclassified = (
        set(df["brewer_name"]) - NON_BREWERY_NAMES
        if n0 else set()
    )
    # Sanity check: the parser should find at least as many rows as this module's
    # documented count, or something in the PDF's layout has shifted.
    if n0 < 30:
        raise RuntimeError(
            f"Parsed only {n0} Resident Brewer rows from {path}; expected roughly 33-34 "
            "-- the PDF's layout may have changed and the parser needs re-checking."
        )

    out = df[~df["brewer_name"].isin(NON_BREWERY_NAMES)].copy()
    log_filter(
        "wv_abca",
        "drop non-beer producers licensed under the Resident Brewer statute "
        f"({sorted(NON_BREWERY_NAMES)})",
        n0, len(out),
    )

    out["state"] = "WV"
    out["wv_abca_id"] = out["brewer_name"]
    return out[["wv_abca_id", "brewer_name", "address_line", "city_state_zip", "county_name", "state"]]


def county_counts() -> pd.DataFrame:
    """Convenience aggregate: brewery count per West Virginia county."""
    df = load()
    counts = df.groupby("county_name").size().rename("wv_abca_count").reset_index()
    return counts
