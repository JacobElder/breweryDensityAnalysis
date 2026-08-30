# Brewery Density Analysis

Ranks US geographies by brewery density and models which places have more
breweries than expected after conditioning on tourism, age structure, income,
college enrollment, and state regulatory regime.

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
  `census_geocoder.py`, `manifest.py`, `shrinkage.py`, `capture_rate_model.py`)
- `scripts/` — pipeline-assembly and analysis scripts: per-state calibration
  (`build_{nc,mi,co,or}_county_dataset.py`), national assembly
  (`build_national_county_dataset.py`, `build_national_cbsa_place_datasets.py`,
  `geocode_national.py`), the two models (`fit_national_models.py`,
  `build_capture_rate_model.py`), and rendered outputs
  (`build_choropleth.py`, `build_top50_table.py`)
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
- `docs/methods_memo.md` — the full methods writeup (every inclusion rule,
  row-count-by-step tables, the complete coverage-error argument, and a
  "what these numbers can't support" section). This README summarizes it;
  read the memo for anything you intend to cite or build on.

## Methodology

**Data sources.** Open Brewery DB (OBDB) is the primary brewery count.
OpenStreetMap (Overpass) was fetched for all 50 states + DC as a secondary
signal but is not incorporated into the headline numbers (see Limitations).
Census County Business Patterns (NAICS 312120) and ACS 5-year (2020-2024)
supply denominators and covariates. TIGER/Line 2025 supplies county/place/CBSA
polygons for geocoding. Four state liquor/ABC licensee registries — NC, MI,
CO, OR — supply independent ground truth for calibration.

**Brewery definition.** OBDB `brewery_type` in `{micro, brewpub, regional,
large, nano}`; excludes `planning`/`closed`, judgment-call categories
(`contract`, `proprietor`, `bar`, `taproom`, `beergarden`, ...), and
cideries/meaderies. Satellite taproom locations are counted as separate rows
by default — tested directly against the alternative (one brewery regardless
of taproom count) using Oregon's OLCC data, which distinguishes the two
explicitly; see Key Findings below.

**Geographic levels.** County, CBSA (metro/micro area), and place are all
built — not just one — because the choice changes the ranking materially (see
Key Findings). CBSA is the recommended primary level; county is secondary;
place is shown only above a 50,000-adults-21+ population floor.

**Two models**, both reading from the same county/CBSA/place datasets:

- **Model A — empirical Bayes shrinkage** (`fit_national_models.py`,
  `src/breweries/shrinkage.py`): partial-pools each county's raw rate toward
  the national mean via a Poisson-Gamma model, fit by method of moments (not
  MLE — MLE was numerically unstable at the place level; see the module
  docstring). Answers "where is density actually high," correcting for the
  small-county-noise problem that dominates a raw-rate ranking. Confidence
  intervals use exact Gamma quantiles, not a normal approximation — the
  posterior is meaningfully skewed for the majority of counties, which have
  0-1 observed breweries.
- **Model B — covariate residual** (`fit_national_models.py`): negative
  binomial regression on log(median household income), median age, college
  enrollment share, tourism establishments per capita, and state fixed
  effects, offset by log(adults 21+). Ranked by shrunken residual
  (observed/expected, itself partially pooled for the same small-count-noise
  reason as Model A). Answers "where are there more breweries than the
  county's own demographic and tourism profile predicts."

**Coverage calibration.** OBDB is a volunteer-maintained dataset and
undercounts true breweries by an amount that varies by state — measured
directly against the four state licensee registries (which independently
track the Brewers Association's 2025 state totals within 1-4%, and are
trusted as ground truth for this reason). A state-only random-intercept model
found state identity explains far more of that variation than local
population density does, so there is no single defensible national
correction factor; states without their own calibration data get a pooled
fallback rate with a deliberately wide uncertainty interval
(`src/breweries/capture_rate_model.py`). The choropleth and ranking tables
are **not** capture-rate-corrected by default — every number in this
project's headline outputs is a raw OBDB count/rate unless stated otherwise.

Full argument, every row-count table, and the "what these numbers can't
support" section: `docs/methods_memo.md`.

## Key findings

- **Boulder County/CBSA/city (CO), Deschutes County/Bend (OR), and Buncombe
  County/Asheville (NC) rank at or near the top at every geographic level** —
  the single most robust result in the data, and a useful face-validity
  anchor for the whole pipeline.
- **OBDB's coverage gap is dominated by which state a brewery is in, not how
  rural the county is.** Measured capture rate: NC 62%, MI 85%, CO 92%, OR
  93%. A regression against local population density found a real but small
  effect (denser counties are somewhat better covered) — several times
  smaller than the state-to-state variation. Treat any national OBDB-only
  ranking as biased against North-Carolina-like regulatory environments
  relative to Colorado/Oregon-like ones.
- **Geographic unit choice changes who's on the list.** Grand Rapids, MI and
  Traverse City, MI both fail to reach the county-level top 20 (Kent County's
  population dilutes the rate; Traverse City's place-level population is
  below the analysis floor) but rank highly at the CBSA level — exactly the
  small-metro case a county- or place-only analysis would have missed.
- **Satellite taprooms roughly double Portland, OR's apparent brewery count
  relative to the rest of the state.** Oregon's OLCC data distinguishes
  primary licenses (285, 96% of the Brewers Association total) from
  "additional location" licenses (347 total, 117% of BA); Multnomah County
  alone accounts for 22 of the 62 additional-location licenses statewide.
  Brewers Association's own count sits almost exactly at the primary-license
  number, which is why "one brewery per independent license" was chosen as
  this project's default over "one row per physical taproom."
- **After conditioning on income, age, college share, and tourism, the
  "outperforming expectations" list is different from the raw-density
  list**: Buncombe NC (Asheville), Charleston SC, Fulton GA (Atlanta), St.
  Louis city MO, Travis TX (Austin), and Richmond city VA top the residual
  ranking — several of these don't appear in the raw top 5 at all, because
  they're beating a *lower* covariate-implied baseline rather than posting
  the highest absolute rate.

## Known limitations and possible next steps

- **Only 4 calibration states.** The state-vs-density finding above is
  robust in direction but the correction model's uncertainty interval is
  necessarily wide (n=4 groups can't support a tight between-state variance
  estimate). Adding 2-3 more states with clean liquor-license open data
  (Washington, Texas, and Georgia all looked promising in early scoping)
  would meaningfully tighten this and let the correction model include
  state-level covariates (e.g., self-distribution law, brewpub statute)
  instead of just an intercept.
- **OSM is fetched but unused nationally.** It's currently only exercised in
  a single-state capture-recapture diagnostic (NC), which found that pairing
  two crowdsourced sources inflates the estimate due to correlated
  visibility bias rather than independent coverage — see
  `docs/methods_memo.md` §5.3. It could still be useful as a third input to a
  proper multi-source capture model (3+ independent-ish sources reduce the
  correlated-bias problem two sources can't escape), but that's a real
  modeling project, not a quick addition.
- **No automated tests.** This pipeline has already had two statistically
  material bugs caught by manual/agent-driven audit (a skewed-CI
  approximation error, a data sentinel-handling gap) rather than by any
  regression check. A small pytest suite covering the shrinkage math
  (`fit_poisson_gamma` against hand-computed values), the capture-rate model,
  and the Chapman capture-recapture estimator would catch a recurrence
  automatically instead of relying on another audit pass.
- **Model B's residual ranking has no holdout validation.** It's currently
  judged only by face validity (do plausible cities show up). A proper
  cross-validated check (e.g., leave-one-state-out prediction error) would
  give a more defensible answer to "how much should you trust the residual
  ranking's precision," beyond "the coefficients have the expected sign."
- **CBSA/place-level top-50 tables don't exist yet** — only the county-level
  one (`build_top50_table.py`) does. Straightforward to add given the CBSA
  and place datasets are already built.
- **An interactive version of the choropleth** (zoom, hover for exact
  county-level numbers, toggle between raw/shrunken/floored) would make the
  county-level detail more explorable than a static PNG allows, at the cost
  of needing a place to host it.

## Codebase audit

This codebase went through a 4-agent parallel correctness audit (statistical
modeling, data-source pipeline, build/analysis scripts, documentation) after
the initial build, plus a follow-up manual review. Real bugs it caught: a
normal-approximation confidence interval on a skewed Gamma posterior that
materially understated uncertainty for low-count counties/places (now exact
Gamma quantiles); an ACS suppressed-data sentinel (`-666666666`) that could
have silently corrupted the adults-21+ denominator for small geographies; a
FIPS zero-padding strip that would have broken joins if a caller didn't
defensively re-pad downstream; the capture-rate correction model mixing an
exposure-weighted aggregate ratio with an unweighted regression slope
(internally inconsistent by construction — now a single weighted regression
supplies both); and that same model's point estimate silently exceeding 1.0
(a "capture rate" over 100%) for the handful of US counties denser than
anything in the 4-state calibration sample (now hard-capped at 1.0).
