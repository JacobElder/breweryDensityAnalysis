"""County-level covariates for the residual model: tourism, college-town share,
income, median age, population density (urban/rural proxy), and population growth.

Tourism proxy: CBP NAICS 721 (Accommodation) establishment count per capita.
College-town proxy: ACS share of population 3+ enrolled in undergrad/grad school.
Density: total population / land area from the TIGER county polygons (ALAND).
Population growth: percent change in ACS total population (B01001_001E) between
an earlier 5-year vintage and the current one (see EARLIER_ACS5_YEAR below) --
unlike the other covariates this one genuinely varies within a state, so it
isn't collinear with the model's state fixed effects.
"""

from __future__ import annotations

import glob
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from breweries.census_client import ACS5_YEAR, ACS_MISSING_SENTINELS, get
from breweries.manifest import log_fetch
from breweries.sources import acs, tiger

RAW_DIR = Path("data/raw/covariates")
NAICS_ACCOMMODATION = "721"
CBP_URL_TEMPLATE = "https://api.census.gov/data/{year}/cbp"
ACS_URL = f"https://api.census.gov/data/{ACS5_YEAR}/acs/acs5"

DEMO_VARS = "NAME,B19013_001E,B01002_001E,B14001_001E,B14001_008E,B14001_009E"

# Earlier ACS5 vintage for the population-growth covariate: roughly 5 years
# before the current vintage, giving a real multi-year comparison window
# without going so far back that ACS methodology/geography changed drastically.
EARLIER_ACS5_YEAR = ACS5_YEAR - 5
EARLIER_ACS_URL = f"https://api.census.gov/data/{EARLIER_ACS5_YEAR}/acs/acs5"
POP_VAR = "B01001_001E"


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


def fetch_population_earlier_national(force: bool = False) -> Path:
    """Pull ACS5 total population (B01001_001E) for the earlier vintage
    (EARLIER_ACS5_YEAR), national, county level -- used as the baseline for
    pop_growth_pct."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / f"population_{EARLIER_ACS5_YEAR}_*.csv")))
    if existing and not force:
        return Path(existing[-1])

    df = get(EARLIER_ACS_URL, {"get": f"NAME,{POP_VAR}", "for": "county:*", "in": "state:*"})

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"population_{EARLIER_ACS5_YEAR}_{ts}.csv"
    df.to_csv(dest, index=False)
    log_fetch(source="acs5_population_earlier", url=EARLIER_ACS_URL, dest_path=str(dest), row_count=len(df),
              notes=f"national, year={EARLIER_ACS5_YEAR}: total population baseline for pop_growth_pct")
    return dest


def _load_pop_growth() -> pd.DataFrame:
    """County-level pop_growth_pct: percent change in ACS total population
    between EARLIER_ACS5_YEAR and the current ACS5_YEAR. Missing/suppressed
    ACS cells in either vintage produce NaN (never silently treated as 0
    growth), matching how the other covariates handle ACS suppression sentinels.
    """
    earlier_path = fetch_population_earlier_national()
    earlier = pd.read_csv(earlier_path, dtype={"state": str, "county": str})
    earlier["state"] = earlier["state"].str.zfill(2)
    earlier["county"] = earlier["county"].str.zfill(3)
    earlier[POP_VAR] = pd.to_numeric(earlier[POP_VAR], errors="coerce")
    earlier.loc[earlier[POP_VAR].isin(ACS_MISSING_SENTINELS), POP_VAR] = pd.NA
    earlier["county_geoid"] = earlier["state"] + earlier["county"]
    earlier = earlier.rename(columns={POP_VAR: "earlier_total_population"})[
        ["county_geoid", "earlier_total_population"]
    ]

    # Current-vintage total population is already fetched/cached by acs.py
    # (same B01001_001E variable, current ACS5_YEAR) -- reuse it rather than
    # re-fetching.
    current = acs.load_national("county")
    current = current.copy()
    current["county_geoid"] = (
        current["state"].astype(str).str.zfill(2) + current["county"].astype(str).str.zfill(3)
    )
    current = current.rename(columns={"total_population": "current_total_population"})[
        ["county_geoid", "current_total_population"]
    ]

    growth = current.merge(earlier, on="county_geoid", how="left")
    growth["pop_growth_pct"] = (
        (growth["current_total_population"] - growth["earlier_total_population"])
        / growth["earlier_total_population"]
        * 100
    )
    return growth[["county_geoid", "pop_growth_pct"]]


def load_county_covariates() -> pd.DataFrame:
    """County-level covariates keyed by (state, county) FIPS: tourism_estab,
    median_household_income, median_age, college_enrollment_share, density_per_sqmi,
    pop_growth_pct.
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

    pop_growth = _load_pop_growth()
    df = df.merge(pop_growth, on="county_geoid", how="left")

    return df


def _load_density() -> pd.DataFrame:
    counties = tiger.load_counties()[["GEOID", "ALAND"]]
    counties = counties.rename(columns={"GEOID": "county_geoid"})
    counties["sqmi"] = counties["ALAND"] / 2_589_988
    return counties[["county_geoid", "sqmi"]]
