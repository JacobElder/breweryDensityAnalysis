# Brewery Density Analysis

Ranks US geographies by brewery density and models which places have more
breweries than expected after conditioning on tourism, age structure, income,
and state regulatory regime.

## Setup

This repo lives under `~/Documents`, which is iCloud Drive-synced. A `.venv`
with thousands of small package files inside an iCloud-synced folder causes
intermittent, hard-to-diagnose `ModuleNotFoundError`s (iCloud creates duplicate
`.pth` files during sync conflicts, and evicts/re-fetches files unpredictably).
The virtual environment is therefore kept **outside** the synced tree:

```bash
export UV_PROJECT_ENVIRONMENT=/Users/jacobelder/.local/venvs/brewery-density-analysis
uv sync
```

Add that `export` to your shell profile, or prefix every `uv` command with it.
Without it, `uv` will silently create `.venv` inside the project folder again
and the import flakiness will come back.

Census API key goes in `.env` (gitignored) as `CENSUS_API_KEY=...` — get one at
https://api.census.gov/data/key_signup.html.

## Layout

- `src/breweries/` — pipeline code (`sources/` per data source, `geocode.py`,
  `census_geocoder.py`, `manifest.py`)
- `scripts/` — one-off assembly scripts per geographic slice (e.g.
  `build_nc_county_dataset.py`)
- `data/raw/` — cached source pulls, timestamped, never re-fetched automatically.
  TIGER/Line geometry is cached as GeoParquet (brotli-compressed) rather than
  the zipped shapefiles Census serves — `src/breweries/sources/tiger.py`
  downloads-and-converts in memory, so no `.zip` ever touches disk. Everything
  else in `data/processed/` is Parquet too (`nc_obdb_osm_match.csv`, a one-off
  manual-audit diagnostic, is the only deliberate exception — CSV stays more
  directly inspectable for that use).
- `data/raw/manifest.jsonl` — append-only log of every fetch and every
  row-dropping filter (before/after counts)
- `data/processed/` — analysis-ready datasets (Parquet) and rendered outputs
  (choropleth PNGs, top-50 table PNG)

## Status

Four-state calibration (NC, MI, CO, OR) against each state's own liquor/ABC
licensee registry, feeding a coverage-correction model
(`src/breweries/capture_rate_model.py`). National pipeline built on top of
that: all 50 states + DC geocoded to county/CBSA/place, covariates (income,
age, college share, tourism) pulled, two ranking models fit
(`scripts/fit_national_models.py` — empirical Bayes shrinkage, and a
negative-binomial residual model with state fixed effects), and a national
choropleth (`scripts/build_choropleth.py`). See `docs/methods_memo.md` for the
full write-up: coverage-error findings, every inclusion rule, and what the
numbers can't support. National OSM pull is a lower-priority third signal, used
so far only for the NC capture-recapture diagnostic (see the memo's Section 5.3
for why that diagnostic backfired informatively rather than being adopted).
