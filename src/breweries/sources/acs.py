"""ACS 5-year population and age-structure data, via the Census API.

Used to compute adults 21+ as the brewery-density denominator. There is no clean
21+ break in the ACS age tables (B01001 brackets at 20 / 21-24), so this project
uses the 21-24 bracket directly rather than interpolating within it — i.e. the
21+ estimate is: (sum of all 25+ brackets) + (21-24 bracket), which slightly
overstates true 21+ population by including ages 21-24 wholesale rather than
prorating out ages 20. That overstatement is small and directionally constant
across geographies, so it should not distort relative rankings.
"""

from __future__ import annotations

import glob
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from breweries.census_client import ACS5_YEAR, ACS_MISSING_SENTINELS, STATE_FIPS, get
from breweries.manifest import log_fetch

RAW_DIR = Path("data/raw/acs")
ACS_URL = f"https://api.census.gov/data/{ACS5_YEAR}/acs/acs5"

# B01001: Sex by Age. Male brackets 21-24 .. 85+, female brackets 21-24 .. 85+.
# (20 and "18 and 19" are separate brackets below 21 and excluded.)
MALE_21PLUS = [
    "B01001_009E",  # 21 years
    "B01001_010E",  # 22-24 years
    "B01001_011E",  # 25-29
    "B01001_012E",  # 30-34
    "B01001_013E",  # 35-39
    "B01001_014E",  # 40-44
    "B01001_015E",  # 45-49
    "B01001_016E",  # 50-54
    "B01001_017E",  # 55-59
    "B01001_018E",  # 60-61
    "B01001_019E",  # 62-64
    "B01001_020E",  # 65-66
    "B01001_021E",  # 67-69
    "B01001_022E",  # 70-74
    "B01001_023E",  # 75-79
    "B01001_024E",  # 80-84
    "B01001_025E",  # 85+
]
FEMALE_21PLUS = [
    "B01001_033E",  # 21 years
    "B01001_034E",  # 22-24 years
    "B01001_035E",  # 25-29
    "B01001_036E",  # 30-34
    "B01001_037E",  # 35-39
    "B01001_038E",  # 40-44
    "B01001_039E",  # 45-49
    "B01001_040E",  # 50-54
    "B01001_041E",  # 55-59
    "B01001_042E",  # 60-61
    "B01001_043E",  # 62-64
    "B01001_044E",  # 65-66
    "B01001_045E",  # 67-69
    "B01001_046E",  # 70-74
    "B01001_047E",  # 75-79
    "B01001_048E",  # 80-84
    "B01001_049E",  # 85+
]
AGE_VARS = MALE_21PLUS + FEMALE_21PLUS
GET_VARS = "NAME,B01001_001E," + ",".join(AGE_VARS)

GEO_FOR = {
    "place": "place:*",
    "county": "county:*",
    "cbsa": "metropolitan statistical area/micropolitan statistical area:*",
}


def fetch(state_abbr: str, geography: str, force: bool = False) -> Path:
    """Pull ACS5 age/sex table for one geography type in a state, or reuse the cache."""
    if geography not in GEO_FOR:
        raise ValueError(f"geography must be one of {list(GEO_FOR)}")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / f"{state_abbr}_{geography}_*.csv")))
    if existing and not force:
        return Path(existing[-1])

    state_fips = STATE_FIPS[state_abbr]
    params = {"get": GET_VARS, "for": GEO_FOR[geography]}
    # CBSAs are not nested within a single state in the Census geographic hierarchy;
    # request nationally and filter to the state's places/counties instead.
    if geography != "cbsa":
        params["in"] = f"state:{state_fips}"

    df = get(ACS_URL, params)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"{state_abbr}_{geography}_{ts}.csv"
    df.to_csv(dest, index=False)

    log_fetch(source="acs5", url=ACS_URL, dest_path=str(dest), row_count=len(df),
              notes=f"state={state_abbr} geography={geography} year={ACS5_YEAR}")
    return dest


def fetch_national(geography: str, force: bool = False) -> Path:
    """Pull ACS5 age/sex table for one geography type, all US, in one call."""
    if geography not in GEO_FOR:
        raise ValueError(f"geography must be one of {list(GEO_FOR)}")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / f"US_{geography}_*.csv")))
    if existing and not force:
        return Path(existing[-1])

    params = {"get": GET_VARS, "for": GEO_FOR[geography]}
    if geography != "cbsa":
        params["in"] = "state:*"

    df = get(ACS_URL, params)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"US_{geography}_{ts}.csv"
    df.to_csv(dest, index=False)

    log_fetch(source="acs5", url=ACS_URL, dest_path=str(dest), row_count=len(df),
              notes=f"national geography={geography} year={ACS5_YEAR}")
    return dest


def _process(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce ACS numeric columns (honoring suppression sentinels) and compute
    total population and adults 21+. Geo-id columns (state/county/place/CBSA)
    are left as strings, exactly as loaded, to preserve zero-padded FIPS codes
    (e.g. Colorado's state FIPS "08"; letting pandas infer them as int would
    silently strip the leading zero and break every downstream FIPS join).
    """
    for col in ["B01001_001E"] + AGE_VARS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
        # ACS uses large negative sentinels (e.g. -666666666) for suppressed/
        # not-applicable cells — never sum these in as if they were real counts.
        df.loc[df[col].isin(ACS_MISSING_SENTINELS), col] = pd.NA

    df["total_population"] = df["B01001_001E"]
    df["adults_21plus"] = df[AGE_VARS].sum(axis=1, min_count=1)

    keep = ["NAME", "total_population", "adults_21plus"]
    geo_cols = [c for c in df.columns if c not in keep and c not in AGE_VARS and c != "B01001_001E"]
    return df[keep + geo_cols]


def load_national(geography: str) -> pd.DataFrame:
    path = fetch_national(geography)
    df = pd.read_csv(path, dtype=str)
    return _process(df)


def load(state_abbr: str, geography: str) -> pd.DataFrame:
    """Load cached ACS data and compute total population and adults 21+."""
    path = fetch(state_abbr, geography)
    df = pd.read_csv(path, dtype=str)
    return _process(df)
