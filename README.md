# Brewery Density Analysis

Ranks US geographies by brewery density and models which places have more
breweries than expected after conditioning on tourism, age structure, income,
college enrollment, and state regulatory regime.

**Interactive map:** https://claude.ai/code/artifact/27565f11-c949-4698-8310-4194090118e7
(county/CBSA toggle, raw/shrunken/floored views, zoom and pan, hover for exact
numbers).

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

Run the test suite with `UV_PROJECT_ENVIRONMENT=... uv run pytest tests/`
(66 tests, statistical-correctness regression coverage — see `tests/`).

## Layout

- `src/breweries/` — pipeline code (`sources/` per data source, `geocode.py`,
  `census_geocoder.py`, `manifest.py`, `shrinkage.py`, `capture_rate_model.py`,
  `capture_recapture.py`)
- `scripts/` — pipeline-assembly and analysis scripts: per-state calibration
  (`build_{state}_county_dataset.py` for nc/mi/co/or/wa/tx/ga/wi/pa), national
  assembly (`build_national_county_dataset.py`,
  `build_national_cbsa_place_datasets.py`, `geocode_national.py`), the two
  models (`fit_national_models.py`, `build_capture_rate_model.py`),
  cross-source diagnostics (`nc_capture_recapture.py`,
  `multi_source_capture_model.py`, `build_obdb_osm_union.py`), validation
  (`validate_model_b_loso.py`), and rendered outputs (`build_choropleth.py`,
  `build_top50_table.py`, `build_top50_cbsa_table.py`,
  `build_top50_place_table.py`, `build_interactive_map.py` +
  `assemble_interactive_map_html.py`)
- `tests/` — pytest regression suite for the statistical modules (shrinkage,
  capture-rate correction, capture-recapture) — hand-derived expected values
  on synthetic data, not just smoke tests
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
  (choropleth PNGs, top-50 table PNGs)
- `docs/methods_memo.md` — the full methods writeup (every inclusion rule,
  row-count-by-step tables, the complete coverage-error argument, and a
  "what these numbers can't support" section). This README summarizes it;
  read the memo for anything you intend to cite or build on.

## Methodology

**Data sources.** Open Brewery DB (OBDB) is the primary brewery count.
OpenStreetMap (Overpass) was fetched for all 50 states + DC as a secondary
signal — not incorporated into the headline numbers, but used in two
diagnostics (see Key Findings). Census County Business Patterns (NAICS
312120) and ACS 5-year (2020-2024) supply denominators and covariates.
TIGER/Line 2025 supplies county/place/CBSA polygons for geocoding. Thirteen
state liquor/ABC/DOR licensee registries — NC, MI, CO, OR, WA, TX, GA, WI, PA,
IL, CA, NY, VA — supply independent ground truth for calibration. Six more
states were investigated: MS, OH, VT, and MN have no bulk-downloadable source
at all; TN, AZ, and SC also lack one but still got a 3-source (OBDB/OSM/CBP)
county dataset used for face-validity checks rather than the correction
model. See Limitations for the full accounting.

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
place is shown only above a 50,000-adults-21+ population floor. Top-50 tables
exist for all three levels.

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
  county's own demographic and tourism profile predicts" — but see the
  leave-one-state-out finding below before treating this ranking as precise.

**Coverage calibration.** OBDB is a volunteer-maintained dataset and
undercounts true breweries by an amount that varies mostly by *state*, not by
how rural a county is — measured directly against 13 state licensee
registries (`src/breweries/capture_rate_model.py`). States without their own
calibration data get a pooled fallback rate from a weighted regression, with
a deliberately wide uncertainty interval reflecting how little a 13-state
sample can support. The choropleth and ranking tables are **not**
capture-rate-corrected by default — every number in this project's headline
outputs is a raw OBDB count/rate unless stated otherwise.

Full argument, every row-count table, and the "what these numbers can't
support" section: `docs/methods_memo.md`.

## Key findings

- **Boulder County/CBSA/city (CO), Deschutes County/Bend (OR), and Buncombe
  County/Asheville (NC) rank at or near the top at every geographic level** —
  the single most robust result in the data, and a useful face-validity
  anchor for the whole pipeline.
- **OBDB's coverage gap is dominated by which state a brewery is in, not how
  rural the county is — and the range keeps widening as more states are
  added.** Measured capture rate across 13 calibration states: VA 46%, GA
  48%, PA 49%, CA 60%, WI 62%, NC 62%, NY 66%, WA 83%, MI 85%, CO 92%, OR
  93%, and IL/TX effectively 100% (see caveat below). A regression against
  local population density found a real but small effect (denser counties
  are somewhat better covered) — several times smaller than the
  state-to-state variation. **Four states' "ground truth" itself has a
  documented quirk, not a pipeline bug**, all inflating the reference count
  above what OBDB could ever match: Wisconsin's DOR brewery-permit category
  sweeps in some non-craft manufacturers (e.g. Anheuser-Busch's Milwaukee
  plant); California's and Virginia's ABC exports count *licenses*/premises
  rather than brands, and several operators hold multiple licenses per
  brand (satellite tasting rooms, alternating proprietorships); Illinois's
  export is cumulative with companion license classes that can double-list
  one site. Texas is the inverse case — its TABC public license table is
  documented by TABC itself to exclude brewpub subordinate authorizations,
  so its reference *undercounts*. The correction model clips every capture
  rate at 1.0 (a rate can't legitimately exceed 100% of a true population)
  rather than let any of these four data quirks invert the correction
  direction.
- **Geographic unit choice changes who's on the list.** Grand Rapids, MI and
  Traverse City, MI both fail to reach the county-level top 20 (Kent County's
  population dilutes the rate; Traverse City's place-level population is
  below the analysis floor) but rank highly at the CBSA level — exactly the
  small-metro case a county- or place-only analysis would have missed. Santa
  Cruz, CA shows the same effect in reverse direction: the city's raw rate
  (11.3/100k) is Boulder/Asheville-tier, but Santa Cruz *County*'s much
  larger population (includes Watsonville and inland areas) dilutes it to
  5.6/100k — a real, manually-verified case, not a data error (see below).
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
  list — but it's less stable than it looks.** In-sample, Buncombe NC
  (Asheville), Charleston SC, Fulton GA (Atlanta), St. Louis city MO, Travis
  TX (Austin), and Richmond city VA top the residual ranking. A
  leave-one-state-out validation (`validate_model_b_loso.py`) found only
  7 of the full-sample top 20 survive when each state is held out of its own
  training data — Buncombe, St. Louis city, and Richmond city hold up
  robustly (Buncombe's residual actually gets *larger* without North
  Carolina in the training set), but **Fulton County's outlier status
  collapses almost entirely without Georgia's own data** — meaning most of
  "more breweries than expected in Fulton" was actually Georgia's fitted
  state effect, not a Fulton-specific signal. Charleston and Travis also
  drop substantially. Read the residual ranking's top few as a starting
  point for investigation, not a validated "go visit these counties" list.
- **A proper multi-source (3-list) capture-recapture model, tried in CO and
  OR where record-level state licensee data made it possible, did not fix
  the correlated-crowdsourcing bias found in the 2-source attempt — it made
  the estimate 4-8x worse.** All three pairwise source-dependence terms
  (OBDB-OSM, OBDB-licensee, OSM-licensee) came out strongly positive, not
  just the suspected OBDB-OSM pair — consistent with general heterogeneity
  in how easy-to-find a brewery is (by any method), which a no-3-way-
  interaction log-linear model has no mechanism to absorb. This is a second,
  independent negative result reinforcing `docs/methods_memo.md` §5.4's
  conclusion: administrative registries, not multi-source crowdsourced
  capture-recapture (2-source or 3-source), are the right calibration
  approach here.
- **A manual deep-dive on Santa Cruz, CA (prompted by a user's firsthand
  knowledge) found one real, confirmed brewery missing from *both* OBDB and
  OSM** (Balefire Brewing Co., opened 2023) — direct evidence that
  crowdsourced sources lag new openings, the exact mechanism this project's
  whole coverage-error argument is built on. A follow-up systematic check
  (`build_obdb_osm_union.py`, unioning OBDB with OSM by name+location match
  rather than extrapolating) found real value but real limits: after fixing
  two bugs in the matching pipeline itself (OBDB records missing lat/lon
  were unmatchable by construction; the name-normalizer stripped "Co" but
  not "Company," systematically missing bank matches), a sample of the
  remaining "OSM-only" candidates *still* contained meaningful false
  positives, e.g. OBDB's compound dual-brand names like "Automatic Brewing
  Co. / Blind Lady Alehouse" not fuzzy-matching a single-brand OSM name. This
  tool cuts the manual-review burden from "investigate every US city" to
  "review a few thousand flagged candidates," but its current output should
  be read as candidates for review, not a validated correction — see
  Limitations.

- **Two of Model B's flagged residual counties got an independent ground-truth
  check from the state-expansion round, with different results.** Virginia's
  ABC data confirms Richmond city genuinely has an outsized brewery count
  relative to its population (10.2 raw rate per 100k, correctly kept
  separate from the much larger, rural Richmond *County* — Virginia's
  independent cities share a name with a same-named county in four cases,
  which required a join-key fix during this integration to avoid silently
  dropping those counties' data). South Carolina has no state licensee
  source, but its OBDB brewery count for Charleston (24) matches CBP's
  independently-collected federal establishment count exactly — suggesting
  Charleston's raw count is not inflated, even though the LOSO validation
  above already showed its *residual* ranking (i.e., relative to
  covariate-predicted expectation) is one of the less stable ones.

## Known limitations and possible next steps

- **Six more states were investigated and found to have no bulk
  brewery/alcohol-license open-data source**: Mississippi, Ohio, Vermont,
  and Minnesota have no usable source at all (checked thoroughly — no state
  open-data portal, only an interactive per-record search tool this
  project's rules don't permit scripting around). Tennessee, Arizona, and
  South Carolina are structurally different: Tennessee's ABC doesn't
  regulate ordinary beer at all (it's licensed locally, city-by-city, with
  no state roll-up); Arizona's and South Carolina's licensing agencies have
  no bulk export, only session-gated interactive lookups. All three still
  got a 3-source (OBDB/OSM/CBP) county dataset built and used for
  face-validity checks (see above), just not folded into the correction
  model, which needs the 4th independent leg to mean anything.
- **The OBDB/OSM union candidate checker (`build_obdb_osm_union.py`) needs
  further work before its output can inform any correction.** Two real bugs
  were found and fixed during development (missing OBDB geocoding before
  matching; incomplete name-suffix stripping), which took the naive estimate
  from "56% more breweries" down to "47% more, of which some are still known
  false positives from compound-name matching and greedy-assignment
  artifacts." Next steps: replace greedy 1:1 record matching with an optimal
  bipartite assignment (e.g. `scipy.optimize.linear_sum_assignment`) to
  eliminate the "one OSM record steals another's correct match" failure
  mode, add compound-name handling (match against each `/`-delimited
  sub-name), and run the same manual spot-check methodology used for Santa
  Cruz across a random sample from multiple states to get a measured
  precision rate before trusting the tool's headline number.
- **Two calibration states' reference data has a known definitional quirk**
  (Wisconsin over-inclusive of non-craft manufacturers, Texas's public table
  under-inclusive of brewpub subordinate licenses) — both are kept in the
  model with the issue documented and, for Texas, actively guarded against
  (rate capped at 1.0) rather than excluded outright, since dropping real
  data without a principled statistical reason is its own bias.
- **Model B's residual ranking is less stable under leave-one-state-out
  validation than its in-sample fit suggests** (Spearman ρ=0.68 between
  full-sample and LOSO rankings, only 7/20 of the top list surviving) — see
  Key Findings. This doesn't invalidate the model, but the top-of-list
  entries should be treated as leads, not conclusions, until the specific
  county in question is checked against which states dominate its estimated
  state effect.
- **An interactive version of the choropleth now exists** (see the link at
  the top of this file) but only at county and CBSA level — place-level
  toggle and a search-by-name box would make it more useful for looking up
  a specific city rather than exploring visually.
- **No automated CI** — the 70-test pytest suite (`tests/`) exists and
  passes but isn't wired into a CI pipeline; a bug could still land on `main`
  without the suite being run.

## Codebase audit

This codebase went through a 4-agent parallel correctness audit (statistical
modeling, data-source pipeline, build/analysis scripts, documentation), a
follow-up manual review, a second round of agent-driven work (6 parallel
agents: 3 new calibration states, a pytest suite, LOSO validation, CBSA/place
tables, plus a multi-source capture model investigation and an OBDB/OSM
union checker), and a third round adding 4 more calibration states (IL, CA,
NY, VA) out of 10 investigated. Real bugs caught across all three rounds:

- A normal-approximation confidence interval on a skewed Gamma posterior that
  materially understated uncertainty for low-count counties/places (now
  exact Gamma quantiles).
- An ACS suppressed-data sentinel (`-666666666`) that could have silently
  corrupted the adults-21+ denominator for small geographies.
- A FIPS zero-padding strip that would have broken joins if a caller didn't
  defensively re-pad downstream.
- The capture-rate correction model mixing an exposure-weighted aggregate
  ratio with an unweighted regression slope (internally inconsistent by
  construction — now a single weighted regression supplies both), and that
  same model's point estimate silently exceeding 1.0 for very dense counties
  (now hard-capped at 1.0, which turned out to matter for a second, different
  reason once Texas was added — see Key Findings).
- Two bugs in the OBDB/OSM record-matching pipeline (missing-geocoding and
  incomplete name-suffix stripping), found while building the union checker
  and fixed in the shared `capture_recapture.py` module.
- A CBSA-level ID-format mismatch in the interactive map's geometry generator
  (path IDs and data IDs used different Census ID formats, silently zero
  matches) and a `Boulder County County, CO`-style double-suffix bug in an
  early draft of the top-50 table, both caught by direct verification before
  publishing.
- `census_geocoder.py`'s batch geocoding crashed with a `KeyError` whenever
  an entire address batch came back with zero matches (a real case: 3 rural
  New York addresses that Census's own geocoder can't resolve) — the
  coordinates column has no comma to split on when every row is `No_Match`,
  so the second column silently doesn't exist. Found while adding New York
  as a calibration state; fixed to reindex the split result so both columns
  always exist regardless of match rate.
- Virginia's independent cities (Fairfax, Franklin, Richmond, Roanoke each
  have both a same-named city and county) collide under TIGER's bare county
  name — the capture-rate model's land-area join used the bare name for
  every state, which would have silently failed to match 8 Virginia
  county/city rows (dropping them from the correction model with no error)
  because Virginia's own data uses the "city"/"County"-suffixed name to
  disambiguate. Fixed by joining against both the bare name and the full
  TIGER `NAMELSAD` for every state, rather than picking one convention.
