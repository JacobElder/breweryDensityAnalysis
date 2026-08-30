"""County Business Patterns (CBP), NAICS 312120 "Breweries" — Census Bureau administrative counts.

This is a genuine administrative establishment count and the best independent
validation available. Two caveats handled explicitly downstream, not silently:

- Brewpubs are frequently classified under NAICS 722511 (full-service restaurants),
  not 312120, so CBP systematically undercounts brewpub-heavy places. A CBP/OBDB
  disagreement is not automatically an OBDB error.
- Small cells can be suppressed for disclosure avoidance. Suppressed values come
  back as missing/null from the API, not zero — they are kept as NaN here, never
  coerced to 0.
"""

from __future__ import annotations

import glob
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from breweries.census_client import CBP_YEAR, STATE_FIPS, get
from breweries.manifest import log_fetch

RAW_DIR = Path("data/raw/cbp")
NAICS_BREWERIES = "312120"
CBP_URL = f"https://api.census.gov/data/{CBP_YEAR}/cbp"


def fetch_county(state_abbr: str, force: bool = False) -> Path:
    """Pull county-level NAICS 312120 establishment counts for a state, or reuse the cache."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / f"{state_abbr}_county_{NAICS_BREWERIES}_*.csv")))
    if existing and not force:
        return Path(existing[-1])

    state_fips = STATE_FIPS[state_abbr]
    df = get(
        CBP_URL,
        {
            "get": "NAME,ESTAB,EMP,EMP_N,NAICS2017",
            "for": "county:*",
            "in": f"state:{state_fips}",
            "NAICS2017": NAICS_BREWERIES,
        },
    )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"{state_abbr}_county_{NAICS_BREWERIES}_{ts}.csv"
    df.to_csv(dest, index=False)

    log_fetch(source="cbp", url=CBP_URL, dest_path=str(dest), row_count=len(df),
              notes=f"state={state_abbr} naics={NAICS_BREWERIES} year={CBP_YEAR}")
    return dest


def load_county(state_abbr: str) -> pd.DataFrame:
    """Load cached CBP county data, preserving suppressed (missing) ESTAB as NaN."""
    path = fetch_county(state_abbr)
    df = pd.read_csv(path, na_values=["", "null", "None"], keep_default_na=True)
    df["ESTAB"] = pd.to_numeric(df["ESTAB"], errors="coerce")
    return df


def fetch_national(force: bool = False) -> Path:
    """Pull county-level NAICS 312120 establishment counts for all US counties in one call."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / f"US_county_{NAICS_BREWERIES}_*.csv")))
    if existing and not force:
        return Path(existing[-1])

    df = get(
        CBP_URL,
        {
            "get": "NAME,ESTAB,EMP,EMP_N,NAICS2017",
            "for": "county:*",
            "in": "state:*",
            "NAICS2017": NAICS_BREWERIES,
        },
    )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"US_county_{NAICS_BREWERIES}_{ts}.csv"
    df.to_csv(dest, index=False)

    log_fetch(source="cbp", url=CBP_URL, dest_path=str(dest), row_count=len(df),
              notes=f"national, naics={NAICS_BREWERIES} year={CBP_YEAR}")
    return dest


def load_county_national() -> pd.DataFrame:
    path = fetch_national()
    df = pd.read_csv(path, na_values=["", "null", "None"], keep_default_na=True)
    df["ESTAB"] = pd.to_numeric(df["ESTAB"], errors="coerce")
    return df
