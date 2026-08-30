"""Shared Census API client: key loading and a thin GET wrapper."""

from __future__ import annotations

import os

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

STATE_FIPS = {
    "NC": "37",
    "MI": "26",
    "CO": "08",
    "OR": "41",
    "WA": "53",
    "TX": "48",
    "GA": "13",
    "WI": "55",
    "PA": "42",
    "MS": "28",
    "CA": "06",
    "NY": "36",
    "VA": "51",
    "OH": "39",
    "IL": "17",
    "VT": "50",
    "TN": "47",
    "AZ": "04",
    "MN": "27",
    "SC": "45",
}

CBP_YEAR = 2023  # latest available CBP vintage as of this pipeline (verified via api.census.gov/data.json)
ACS5_YEAR = 2024  # latest available ACS 5-year vintage as of this pipeline (2020-2024 estimates)

# ACS tables use large negative sentinels for suppressed/not-applicable cells
# (typically geographies too small to produce a reliable estimate). These are
# never literal counts and must be coerced to missing (NaN), not summed/averaged
# in as if they were real values. Shared by every module that reads ACS estimate
# columns (acs.py, covariates.py).
ACS_MISSING_SENTINELS = {-666666666, -222222222, -333333333, -555555555, -888888888, -999999999}


def api_key() -> str:
    key = os.environ.get("CENSUS_API_KEY")
    if not key:
        raise RuntimeError(
            "CENSUS_API_KEY not set. Get a free key at "
            "https://api.census.gov/data/key_signup.html and add it to .env as "
            "CENSUS_API_KEY=... "
        )
    return key


def get(url: str, params: dict) -> pd.DataFrame:
    """GET a Census API endpoint and return the JSON-array-of-arrays response as a DataFrame."""
    params = {**params, "key": api_key()}
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    rows = resp.json()
    return pd.DataFrame(rows[1:], columns=rows[0])
