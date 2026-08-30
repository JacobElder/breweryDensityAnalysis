"""Open Brewery DB source: fetch the combined CSV, filter to a state, apply inclusion rules.

Source: https://github.com/openbrewerydb/openbrewerydb (community-maintained; closures lag,
so stale listings inflate counts, especially in regions with high recent closure activity).
"""

from __future__ import annotations

import glob
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from breweries.manifest import log_fetch, log_filter

RAW_URL = "https://raw.githubusercontent.com/openbrewerydb/openbrewerydb/master/breweries.csv"
RAW_DIR = Path("data/raw/obdb")

# Physical, currently-operating brewing locations. This is the project's default
# brewery definition — see docs/methods.md for the full inclusion-rule rationale.
INCLUDE_TYPES = {"micro", "brewpub", "regional", "large", "nano"}

# Explicitly out per the handoff: not currently brewing.
DEFINITIONALLY_EXCLUDE_TYPES = {"planning", "closed"}

# Judgment calls: excluded from the primary definition because they don't clearly
# correspond to an independent physical brewing location. Kept in the raw data
# and available for sensitivity checks.
JUDGMENT_EXCLUDE_TYPES = {"contract", "proprietor", "bar", "taproom", "beergarden", "beer brand", "location"}

# Cideries/meaderies are a separate beverage category; excluded from the brewery count.
CIDERY_TYPES = {"cidery", "meadery"}


def fetch(force: bool = False) -> Path:
    """Download the OBDB combined CSV, or reuse the existing cached copy."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(glob.glob(str(RAW_DIR / "breweries_*.csv")))
    if existing and not force:
        return Path(existing[-1])

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RAW_DIR / f"breweries_{ts}.csv"
    resp = requests.get(RAW_URL, timeout=60)
    resp.raise_for_status()
    dest.write_bytes(resp.content)

    df = pd.read_csv(dest)
    log_fetch(source="obdb", url=RAW_URL, dest_path=str(dest), row_count=len(df))
    return dest


# Known data-entry error in the upstream CSV: one Missouri brewery is tagged
# "MIssouri" (capital I), which would otherwise silently create a fake 52nd state.
STATE_NAME_FIXES = {"MIssouri": "Missouri"}


def load_us(country: str = "United States") -> pd.DataFrame:
    """Load the cached OBDB CSV filtered to all US records (every state + DC)."""
    path = fetch()
    df = pd.read_csv(path)
    n0 = len(df)

    df_us = df[df["country"] == country].copy()
    log_filter("obdb", f"country == {country!r}", n0, len(df_us))

    n_typo = (df_us["state_province"].isin(STATE_NAME_FIXES)).sum()
    df_us["state_province"] = df_us["state_province"].replace(STATE_NAME_FIXES)
    if n_typo:
        log_filter("obdb", "fix state_province data-entry typos", len(df_us), len(df_us),
                   notes=f"corrected {STATE_NAME_FIXES} on {n_typo} row(s)")

    return df_us.reset_index(drop=True)


def load_state(state_province: str, country: str = "United States") -> pd.DataFrame:
    """Load the cached OBDB CSV and filter to one state, logging every step."""
    path = fetch()
    df = pd.read_csv(path)
    n0 = len(df)

    df_country = df[df["country"] == country]
    log_filter("obdb", f"country == {country!r}", n0, len(df_country))

    df_state = df_country[df_country["state_province"] == state_province]
    log_filter("obdb", f"state_province == {state_province!r}", len(df_country), len(df_state))

    return df_state.reset_index(drop=True)


def apply_inclusion_rule(df: pd.DataFrame, source_label: str) -> pd.DataFrame:
    """Apply the project's default brewery_type inclusion rule, logging the type breakdown."""
    n0 = len(df)
    type_counts = df["brewery_type"].value_counts().to_dict()

    included = df[df["brewery_type"].isin(INCLUDE_TYPES)]

    excluded_types = set(df["brewery_type"].unique()) - INCLUDE_TYPES
    breakdown = {t: c for t, c in type_counts.items() if t in excluded_types}
    notes = f"kept={sorted(INCLUDE_TYPES)}; dropped_by_type={breakdown}"

    log_filter("obdb", f"brewery_type inclusion rule ({source_label})", n0, len(included), notes=notes)
    return included.reset_index(drop=True)
