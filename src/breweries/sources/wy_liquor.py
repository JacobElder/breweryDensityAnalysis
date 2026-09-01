"""Wyoming Department of Revenue, Liquor Division -- "Wyoming Malt Beverage
Wholesaler List" PDF.

Source: linked directly from the Liquor Division's own public homepage,
https://liquor365.wyo.gov/ (found under a "Wyoming Malt Beverage Wholesaler List"
button; fetched here by parsing that button's href out of the homepage HTML rather
than hardcoding the underlying CMS file ID, since the ID is issued by a Dynamics 365
content-management backend and could change on re-upload). This is a genuine bulk,
no-login PDF -- the entire statewide roster in one document, refreshed same-day as of
the 2026-08-31 fetch (`last-modified` header dated 2026-08-31) -- not the defunct
eliquor.wyoming.gov / wld.encompass8.com interactive lookup (that domain no longer
resolves at all; Wyoming's e-licensing vendor migrated to `liquor365.wyo.gov`, a
Microsoft Dynamics 365 Power Pages portal, sometime before this fetch). No
Wyoming state open-data portal (opendata.wyo.gov / data.wyo.gov) resolves either.

## Why this is the brewery source, not the separate "License Holder List" PDF

liquor365.wyo.gov also publishes a "Wyoming Liquor License Holder List" PDF, but that
one is explicitly scoped ("Authorized to Sell Alcoholic Beverages at Retail to
Consumers") to *retail* licenses -- Restaurant, Bar, Bar & Grill, Package Store,
Limited Retail (Club), and a retail "Manufacturer License" class that, on inspection,
is used exclusively by spirits-distillery tasting rooms (Arcola Distillery, Backwards
Distilling, Brush Creek Distillery, Chronicles Distilling, etc.) -- zero Wyoming
breweries appear under it. Wyoming instead licenses brewers under its wholesale
scheme: W.S. 12-4-201 lets a resident brewer act as its own wholesale distributor from
its place of manufacture, so brewers show up on the "Malt Beverage Wholesaler List"
alongside genuine third-party beer/wine wholesale distributors. That list carries a
`County` column (WY's own administrative field, used directly here rather than
geocoding, same precedent as il_liquor.py/pa_liquor.py) and is the one parsed by this
module.

## Inclusion rule: 58 wholesaler rows, 28 are physical breweries

The Wholesaler List has no field separating "brews its own beer" from "distributes
other brands' beer" -- both hold the identical Malt Beverage Wholesaler license class.
With only 58 rows statewide, every row was checked individually (business name,
web presence) and classified by hand; the module enforces this at runtime by raising
if a future fetch contains a License Holder name not already in one of the two
explicit sets below, so a schema/roster change surfaces immediately rather than
silently mis-classifying.

Kept as breweries (28): standalone production breweries and brewpubs, identified by
name/product line -- e.g. Bad Joker Brewing, Badass Brews, Smith Alley Brewing (dba of
Big Horn Public House), Blue Raven Brewery, Bond's Brewing, Cody Craft Brewing, Cowboy
State Brewing, Cygnet Brewing, Altitude Chophouse & Brewery (dba of First Street
Station), Freedoms Edge Brewing, Melvin Brewing (dba of Get Down LLC), Roadhouse
Brewing (dba of Get Funky LLC), Gruner Brothers Brewing, Snowy Mountain Brewery (dba
of International Resort Properties), Lander Brewing, Millstone Pizza Company &
Brewery, Mountain Hops Brewhouse, One Eyed Buffalo Brewing (dba of OEB LLC), Oil City
Beer Company, Pushroot Brewing (dba of Pushroot Lagerhaus), Shades Brewing (dba of
Shades of Pale), The Library Sports Grille & Brewery (dba of Sherlock Investments),
Skull Tree Brewing, Square State Brewing, Jackson Hole Pub & Brewery/Snake River
Brewing (dba of SRB Operations), Stahoo's Brewery and Taproom, Stillwest Brewery and
Grill (dba of Teton Brewing Company), Wind River Brewing.

Dropped as non-breweries (30): third-party wholesale beer/wine distributors with no
production of their own -- Big Horn Beverage Company, Cheyenne Beverage/Bison Beverage
(6 rows: 1 main + 5 satellites), G & G Enterprises/Smith Beverages, Quality Brands
Distribution (4 rows), T & N Distributing, Teton Distributors (9 rows: 1 main + 8
satellites), The Odom Corporation (a large national beverage distributor), Valley High
Distribution/Roadhouse Distribution Company (the wholesale-distribution *arm* of
Roadhouse Brewing, licensed separately from the brewery itself and correctly kept
distinct -- same one-brewer-two-license-rows pattern documented for out-of-state
manufacturers in il_liquor.py/ga_dor.py), Western Wyoming Beverages (2 rows), and
Yellowstone Country Distributing; plus two non-brewery manufacturers -- Big Lost
Meadery (a meadery, excluded per this project's standing cidery/meadery convention,
same as OBDB's own CIDERY_TYPES exclusion) and Blind Tiger Brands (a beverage brand/
importer with no identifiable Wyoming production site).

## Data-quality caveat: this list itself undercounts Wyoming breweries

Unlike every other liquor-registry source calibrated in this project, WY's raw
capture rate (obdb_count / liquor_count, computed in build_wy_county_dataset.py) comes
out to 142.9% (40 OBDB rows vs. only 28 on this list) -- OBDB has *more* Wyoming
breweries than the Wholesaler List does, not fewer. W.S. 12-4-201 lets a resident
brewer self-distribute, but does not require it: a brewery that instead sells only
on-premises or contracts with a genuine third-party wholesaler for off-premises sales
never needs its own Malt Beverage Wholesaler license and so never appears on this
list at all. This list is therefore not a ceiling on Wyoming's true brewery count the
way IL's or PA's licensing rolls are -- it is itself an undercount, biased toward
breweries large/established enough to self-distribute. Both counts still under the
Brewers Association's total of 49 (2025), consistent with this list omitting some
real, OBDB-verified breweries rather than either source overcounting.
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

RAW_DIR = Path("data/raw/wy_liquor")
HOMEPAGE_URL = "https://liquor365.wyo.gov/"
LIST_LINK_TITLE = "Wyoming Malt Beverage Wholesaler List"

WY_COUNTIES = {
    "ALBANY", "BIG HORN", "CAMPBELL", "CARBON", "CONVERSE", "CROOK", "FREMONT",
    "GOSHEN", "HOT SPRINGS", "JOHNSON", "LARAMIE", "LINCOLN", "NATRONA", "NIOBRARA",
    "PARK", "PLATTE", "SHERIDAN", "SUBLETTE", "SWEETWATER", "TETON", "UINTA",
    "WASHAKIE", "WESTON",
}

BREWERY_NAMES = {
    "BAD JOKER BREWING COMPANY INC BAD JOKER BREWING COMPANY",
    "BADASS BREWS LLC BADASS BREWS",
    "BIG HORN PUBLIC HOUSE LLC SMITH ALLEY BREWING",
    "BLUE RAVEN BREWERY LLC BLUE RAVEN BREWERY",
    "BOND'S BREWING COMPANY LLC BOND'S BREWING COMPANY",
    "CODY CRAFT BREWING LLC CODY CRAFT BREWING",
    "COWBOY STATE BREWING LLC COWBOY STATE BREWING",
    "CYGNET BREWING LLC CYGNET BREWING COMPANY",
    "FIRST STREET STATION INC ALTITUDE CHOPHOUSE & BREWERY",
    "FREEDOMS EDGE BREWING COMPANY LLC FREEDOMS EDGE BREWING COMPANY",
    "GET DOWN LLC MELVIN BREWING COMPANY",
    "GET FUNKY LLC ROADHOUSE BREWING COMPANY",
    "GRUNER BROTHERS BREWING GRUNER BROTHERS BREWING",
    "INTERNATIONAL RESORT PROPERTIES LLLP SNOWY MOUNTAIN BREWERY",
    "LANDER BREWING COMPANY LLC LANDER BREWING COMPANY",
    "MILLSTONE PIZZA LLC MILLSTONE PIZZA COMPANY & BREWERY",
    "MOUNTAIN HOPS BREWHOUSE LLC MOUNTAIN HOPS BREWHOUSE",
    "OEB LLC ONE EYED BUFFALO BREWING COMPANY",
    "OIL CITY BEER COMPANY LLC OIL CITY BEER COMPANY",
    "PUSHROOT LAGERHAUS LLC PUSHROOT BREWING COMPANY",
    "SHADES OF PALE INC SHADES BREWING",
    "SHERLOCK INVESTMENTS LLC THE LIBRARY SPORTS GRILLE & BREWERY",
    "SKULL TREE BREWING LLC SKULL TREE BREWING",
    "SQUARE STATE BREWING INC SQUARE STATE BREWING COMPANY",
    "SRB OPERATIONS LLC JACKSON HOLE PUB & BREWERY SNAKE RIVER BREWING",
    "STAHOO'S BREWERY AND TAPROOM LLC STAHOO'S BREWERY AND TAPROOM",
    "TETON BREWING COMPANY LLC STILLWEST BREWERY AND GRILL",
    "WIND RIVER BREWING COMPANY INC WIND RIVER BREWING COMPANY",
}

NON_BREWERY_NAMES = {
    "BIG HORN BEVERAGE COMPANY INC BIG HORN BEVERAGE COMPANY",
    "BIG LOST MEADERY LLC BIG LOST MEADERY",
    "BLIND TIGER BRANDS LLC BLIND TIGER BRANDS",
    "CHEYENNE BEVERAGE INC BISON BEVERAGE",
    "CHEYENNE BEVERAGE INC BISON BEVERAGE SATELLITE",
    "G & G ENTERPRISES INC SMITH BEVERAGES",
    "OSPREY BEVERAGES LLC OSPREY BEVERAGES",
    "QUALITY BRANDS DISTRIBUTION LLC QUALITY BRANDS OF CASPER SATELLITE",
    "QUALITY BRANDS DISTRIBUTION LLC QUALITY BRANDS OF CHEYENNE",
    "QUALITY BRANDS DISTRIBUTION LLC QUALITY BRANDS OF CODY-SATELLITE",
    "QUALITY BRANDS DISTRIBUTION LLC QUALITY BRANDS OF RIVERTON SATELLITE",
    "T & N DISTRIBUTING LLC T & N DISTRIBUTING",
    "TETON DISTRIBUTORS INC TETON DISTRIBUTORS",
    "TETON DISTRIBUTORS INC TETON DISTRIBUTORS SATELLITE",
    "THE ODOM CORPORATION THE ODOM CORPORATION",
    "VALLEY HIGH DISTRIBUTION LLC ROADHOUSE DISTRIBUTION COMPANY",
    "WESTERN WYOMING BEVERAGES INC WESTERN WYOMING BEVERAGES",
    "WESTERN WYOMING BEVERAGES INC WESTERN WYOMING BEVERAGES SATELLITE",
    "YELLOWSTONE COUNTRY DISTRIBUTING YELLOWSTONE COUNTRY DISTRIBUTING",
}

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def _find_list_url(homepage_html: str) -> str:
    m = re.search(
        r'title="' + re.escape(LIST_LINK_TITLE) + r'"\s+href="([^"]+)"',
        homepage_html,
    )
    if not m:
        raise RuntimeError(
            f"Could not find a link titled {LIST_LINK_TITLE!r} on {HOMEPAGE_URL} -- "
            "the Liquor Division may have renamed or moved this document."
        )
    return m.group(1)


def fetch(force: bool = False) -> Path:
    """Download the current Wyoming Malt Beverage Wholesaler List PDF, or reuse cache."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "wy_malt_beverage_wholesaler_list_*.pdf")))
    if existing and not force:
        return Path(existing[-1])

    home = requests.get(HOMEPAGE_URL, headers=_HEADERS, timeout=30)
    home.raise_for_status()
    list_url = _find_list_url(home.text)

    resp = requests.get(list_url, headers=_HEADERS, timeout=60)
    resp.raise_for_status()
    if resp.headers.get("content-type", "").split(";")[0] != "application/pdf":
        raise RuntimeError(
            f"Expected a PDF response from {list_url}; got "
            f"content-type={resp.headers.get('content-type')!r} -- the document link "
            "may have changed format."
        )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"wy_malt_beverage_wholesaler_list_{ts}.pdf"
    dest.write_bytes(resp.content)

    log_fetch(
        source="wy_liquor", url=list_url, dest_path=str(dest),
        notes=f"Wyoming Malt Beverage Wholesaler List PDF, linked from {HOMEPAGE_URL}",
    )
    return dest


def _parse_pdf(path: Path) -> pd.DataFrame:
    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() for page in reader.pages)
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    rows = []
    for j, line in enumerate(lines):
        if line.upper() in WY_COUNTIES:
            rows.append({
                "license_holder_dba": lines[j - 3],
                "address_line": lines[j - 2],
                "phone": lines[j - 1],
                "county_name": line.upper().title(),
            })
    return pd.DataFrame(rows)


def load() -> pd.DataFrame:
    """Load the cached PDF, parse it into rows, and apply the brewery/non-brewery
    classification documented in the module docstring."""
    path = fetch()
    df = _parse_pdf(path)
    n0 = len(df)

    unclassified = set(df["license_holder_dba"]) - BREWERY_NAMES - NON_BREWERY_NAMES
    if unclassified:
        raise RuntimeError(
            f"New/unrecognized License Holder & DBA name(s) in WY Malt Beverage "
            f"Wholesaler List: {unclassified} -- classify as brewery or non-brewery "
            "in BREWERY_NAMES / NON_BREWERY_NAMES before proceeding."
        )

    out = df[df["license_holder_dba"].isin(BREWERY_NAMES)].copy()
    log_filter(
        "wy_liquor",
        "license_holder_dba in BREWERY_NAMES (28 hand-classified physical breweries, "
        "dropping third-party wholesale distributors and non-beer manufacturers)",
        n0, len(out),
    )

    out["state"] = "WY"
    out["wy_liquor_id"] = out["license_holder_dba"]
    return out[["wy_liquor_id", "license_holder_dba", "address_line", "county_name", "state"]]


def county_counts() -> pd.DataFrame:
    """Convenience aggregate: brewery count per Wyoming county."""
    df = load()
    counts = df.groupby("county_name").size().rename("wy_liquor_count").reset_index()
    return counts
