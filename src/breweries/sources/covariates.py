"""County-level covariates for the residual model: tourism, college-town share,
income, median age, and population density (urban/rural proxy).

Tourism proxy: CBP NAICS 721 (Accommodation) establishment count per capita.
College-town proxy: ACS share of population 3+ enrolled in undergrad/grad school.
Density: total population / land area from the TIGER county polygons (ALAND).
"""

from __future__ import annotations

import glob
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd

from breweries.census_client import ACS5_YEAR, get
from breweries.manifest import log_fetch

RAW_DIR = Path("data/raw/covariates")
NAICS_ACCOMMODATION = "721"
CBP_URL_TEMPLATE = "https://api.census.gov/data/{year}/cbp"
ACS_URL = f"https://api.census.gov/data/{ACS5_YEAR}/acs/acs5"

DEMO_VARS = "NAME,B19013_001E,B01002_001E,B14001_001E,B14001_008E,B14001_009E"


def fetch_tourism_national(year: int = 2023, force: bool = False) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / f"tourism_cbp{NAICS_ACCOMMODATION}_*.csv")))
    if existing and not force:
        return Path(existing[-1])

    url = CBP_URL_TEMPLATE.format(year=year)
    df = get(url, {"get": "NAME,ESTAB,EMP", "for": "county:*", "in": "state:*",
                    "NAICS2017": NAICS_ACCOMMODATION})

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"tourism_cbp{NAICS_ACCOMMODATION}_{ts}.csv"
    df.to_csv(dest, index=False)
    log_fetch(source="cbp_tourism", url=url, dest_path=str(dest), row_count=len(df),
              notes=f"national, naics={NAICS_ACCOMMODATION} (Accommodation) year={year}")
    return dest


def fetch_demographics_national(force: bool = False) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "demographics_*.csv")))
    if existing and not force:
        return Path(existing[-1])

    df = get(ACS_URL, {"get": DEMO_VARS, "for": "county:*", "in": "state:*"})

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"demographics_{ts}.csv"
    df.to_csv(dest, index=False)
    log_fetch(source="acs5_demographics", url=ACS_URL, dest_path=str(dest), row_count=len(df),
              notes=f"national, year={ACS5_YEAR}: median income, median age, college enrollment")
    return dest


def load_county_covariates() -> pd.DataFrame:
    """County-level covariates keyed by (state, county) FIPS: tourism_estab,
    median_household_income, median_age, college_enrollment_share, density_per_sqmi.
    """
    tourism_path = fetch_tourism_national()
    tourism = pd.read_csv(tourism_path, dtype={"state": str, "county": str})
    tourism["state"] = tourism["state"].str.zfill(2)
    tourism["county"] = tourism["county"].str.zfill(3)
    tourism = tourism.rename(columns={"ESTAB": "tourism_estab"})[["state", "county", "tourism_estab"]]

    demo_path = fetch_demographics_national()
    demo = pd.read_csv(demo_path, dtype={"state": str, "county": str})
    demo["state"] = demo["state"].str.zfill(2)
    demo["county"] = demo["county"].str.zfill(3)
    # ACS uses large negative sentinels (e.g. -666666666) for suppressed/inapplicable
    # cells (typically counties too small to estimate reliably) — never average these in.
    ACS_MISSING_SENTINELS = {-666666666, -222222222, -333333333, -555555555, -888888888, -999999999}
    for col in ["B19013_001E", "B01002_001E", "B14001_001E", "B14001_008E", "B14001_009E"]:
        demo[col] = pd.to_numeric(demo[col], errors="coerce")
        demo.loc[demo[col].isin(ACS_MISSING_SENTINELS), col] = pd.NA
    demo["median_household_income"] = demo["B19013_001E"]
    demo["median_age"] = demo["B01002_001E"]
    demo["college_enrollment_share"] = (demo["B14001_008E"] + demo["B14001_009E"]) / demo["B14001_001E"]

    df = demo[["state", "county", "NAME", "median_household_income", "median_age",
               "college_enrollment_share"]].merge(tourism, on=["state", "county"], how="left")
    df["tourism_estab"] = df["tourism_estab"].fillna(0)
    df["county_geoid"] = df["state"] + df["county"]

    density = _load_density()
    df = df.merge(density, on="county_geoid", how="left")

    return df


def _load_density() -> pd.DataFrame:
    county_zip = sorted(glob.glob("data/raw/tiger/us_county_*.zip"))[-1]
    counties = gpd.read_file(f"zip://{county_zip}")[["GEOID", "ALAND"]]
    counties = counties.rename(columns={"GEOID": "county_geoid"})
    counties["sqmi"] = counties["ALAND"] / 2_589_988
    return counties[["county_geoid", "sqmi"]]
