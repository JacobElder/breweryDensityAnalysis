"""Shared pytest fixtures for the breweries test suite.

The `breweries` package is installed editable into the project's uv
environment (see pyproject.toml's `[tool.hatch.build.targets.wheel]`), so no
sys.path manipulation is required here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed"


@pytest.fixture(scope="session")
def us_county_analysis_path() -> Path:
    """Path to the real cached county-level dataset, used for a single
    integration-style smoke test per module (see module docstrings). Skips
    the test if the file isn't present rather than failing, since this repo
    directory may not always have data/ populated (e.g. fresh clone / CI).
    """
    path = PROCESSED_DATA_DIR / "us_county_analysis.parquet"
    if not path.exists():
        pytest.skip(f"real cached dataset not found at {path}")
    return path


@pytest.fixture(scope="session")
def nc_obdb_osm_match_path() -> Path:
    """Path to the real cached match_records() output for North Carolina
    (see scripts/nc_capture_recapture.py). Skips rather than fails if
    absent.
    """
    path = PROCESSED_DATA_DIR / "nc_obdb_osm_match.csv"
    if not path.exists():
        pytest.skip(f"real cached dataset not found at {path}")
    return path
