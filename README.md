# Brewery Density Analysis

Ranks US geographies by brewery density and models which places have more
breweries than expected after conditioning on tourism, age structure, income,
college enrollment, and state regulatory regime.

**Interactive map:** https://claude.ai/code/artifact/27565f11-c949-4698-8310-4194090118e7
(county/CBSA toggle, raw/shrunken/floored views, zoom and pan, hover for exact
numbers, and a calibration-confidence overlay — hatched counties/CBSAs sit in
states without a directly-measured OBDB capture rate, so their coverage
estimate is a much wider extrapolation; toggle it off to see the map
unannotated). A **Table** tab sits alongside the map — a searchable,
sortable listing of the same county/CBSA data for finding a specific place by
name rather than hunting visually; clicking a row jumps back to the map with
that county/CBSA selected.

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
  `capture_recapture.py`, `map_labels.py` — collision-aware, data-driven
  choropleth label placement, see Key Findings)
- `scripts/` — pipeline-assembly and analysis scripts: per-state calibration
  (`build_{state}_county_dataset.py` for nc/mi/co/or/wa/tx/ga/wi/pa/il/ca/ny/va,
  plus a 3-source-only variant for az/tn/sc that lack a licensee registry),
  national assembly (`build_national_county_dataset.py`,
  `build_national_cbsa_place_datasets.py`, `geocode_national.py`), the two
  models (`fit_national_models.py`, `build_capture_rate_model.py`) plus
  `build_corrected_rankings.py` (applies the capture-rate correction at
  national scale and compares it to the raw ranking — see Key Findings),
  cross-source diagnostics (`nc_capture_recapture.py`,
  `multi_source_capture_model.py`, `build_obdb_osm_union.py`,
  `test_capture_rate_drivers.py`), validation (`validate_model_b_loso.py`),
  further analysis (`build_spatial_hotspots.py` — Moran's I / Getis-Ord Gi*
  hot-spot clustering, `build_brewery_deserts.py` — the inverse ranking,
  `build_state_rollup_table.py` — state-level summary), and rendered outputs
  (`build_choropleth.py`, `build_map_comparison.py` — raw/shrunken/corrected
  side by side, `build_top50_table.py`, `build_top50_cbsa_table.py`,
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
TIGER/Line 2025 supplies county/place/CBSA polygons for geocoding.
Twenty-three state/DC liquor/ABC/DOR licensee registries — NC, MI, CO, OR,
WA, TX, GA, WI, PA, IL, CA, NY, VA, KY, FL, CT, MA, MO, NE, NJ, WV, WY, DC —
supply independent ground truth for calibration. Every other state was
investigated for a usable bulk source across two rounds; 28 states have none
(an interactive-only search tool, a login-gated portal, bot/WAF protection,
or no centralized state-level registry at all — the reasons vary and aren't
interchangeable), of which MS, OH, VT, MN, TN, AZ, and SC additionally got a
3-source (OBDB/OSM/CBP) county dataset used for face-validity checks rather
than the correction model. See Limitations for the full accounting.

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
  enrollment share, tourism establishments per capita, 5-year county
  population growth rate, and state fixed effects, offset by log(adults
  21+). Ranked by shrunken residual (observed/expected, itself partially
  pooled for the same small-count-noise reason as Model A). Answers "where
  are there more breweries than the county's own demographic and tourism
  profile predicts" — but see the leave-one-state-out finding below before
  treating this ranking as precise. (Population growth was added and tested
  as a candidate covariate specifically because it's one of the few
  plausible drivers that varies *within* a state — a state-level-only
  covariate like an excise tax rate would be collinear with the state fixed
  effects already in the model and couldn't be estimated; see Key Findings
  for the result.)

**Coverage calibration.** OBDB is a volunteer-maintained dataset and
undercounts true breweries by an amount that varies mostly by *state*, not by
how rural a county is — measured directly against 23 state/DC licensee
registries (`src/breweries/capture_rate_model.py`). States without their own
calibration data get a pooled fallback rate from a weighted regression, with
a deliberately wide uncertainty interval reflecting how little a 23-state
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
  added.** Measured capture rate across 23 calibration states/DC: VA 46%, GA
  48%, KY 54%, PA 49%, CT 58%, CA 60%, WI 62%, NC 62%, DC 64%, NY 66%, FL
  76%, NE 76%, NJ 75%, MA 83%, WA 83%, MI 85%, CO 92%, OR 93%, and
  WV/TX/IL/WY/MO effectively 100% (clipped — see caveat below). A regression
  against local population density found a real but small effect (denser
  counties are somewhat better covered) — several times smaller than the
  state-to-state variation. **Six states' "ground truth" itself has a
  documented quirk, not a pipeline bug**, all inflating the reference count
  above what OBDB could ever match: Wisconsin's DOR brewery-permit category
  sweeps in some non-craft manufacturers (e.g. Anheuser-Busch's Milwaukee
  plant); California's and Virginia's ABC exports count *licenses*/premises
  rather than brands, and several operators hold multiple licenses per
  brand (satellite tasting rooms, alternating proprietorships); Illinois's
  export is cumulative with companion license classes that can double-list
  one site; Missouri's "Microbrewery" license category structurally excludes
  the state's own large/regional breweries (Anheuser-Busch, Boulevard),
  which drives its raw ratio to 166% — the highest of any calibration state.
  Two are the inverse case, where the *reference* undercounts: Texas's TABC
  public license table is documented by TABC itself to exclude brewpub
  subordinate authorizations, and Wyoming's wholesaler-list source only
  captures breweries that self-distribute. The correction model clips every
  capture rate at 1.0 (a rate can't legitimately exceed 100% of a true
  population) rather than let any of these six data quirks invert the
  correction direction.
- **The 23 calibrated states/DC cover 70% of the US adult population and
  72% of OBDB-listed breweries** despite being fewer than half of all
  states/jurisdictions — because several of the largest states by both
  population and brewery count (CA, TX, NY, PA, IL, FL) are among them. The
  remaining ~28% of OBDB breweries sit in states relying on the much wider
  pooled-extrapolation interval — though see the state-level rollup finding
  below on why that gap matters more than the percentage alone suggests.
- **Applying the correction at national scale for the first time reshuffles
  the ranking, and does so unevenly** (`scripts/build_corrected_rankings.py`,
  `data/processed/us_county_raw_vs_corrected_rankings.csv`). Counties in
  low-capture-rate states jump sharply once corrected — Richmond VA (raw
  rank 25 → corrected rank 7), Coconino AZ (21→6), Crawford PA (49→22),
  Henrico VA (+138 ranks), Chatham GA (+132) — while high-capture-rate
  states barely move (Boulder CO 1→9, Deschutes OR 2→11) since there's
  little room left to correct upward. **Texas counties actually drop**
  (Travis 159→323, Comal 237→401, both -164 ranks): TX's raw capture ratio
  is 122% (an artifact of TABC's incomplete reference, see above), which
  gets clipped to exactly 1.0 — meaning TX gets *zero* upward correction
  while every other state's counties get inflated relative to it. This is a
  real, code-verified consequence of the >100%-clipping logic, not a bug,
  but it's counterintuitive enough to flag explicitly: a state's *raw*
  capture rate exceeding 100% does not mean "well-covered," it means "the
  reference itself needs its own correction," and the model has no way to
  express that distinction once it clips to 1.0.
- **What actually drives a state's OBDB capture rate remains statistically
  unexplained.** Two candidate hypotheses were tested directly against the
  13 calibrated states (`scripts/test_capture_rate_drivers.py`): whether
  capture rate degrades as a state's true brewery count grows (a
  crowdsourcing-capacity argument — weak, statistically unreliable hint,
  Pearson r=-0.37, Spearman ρ=-0.45, both non-significant at n=13) and
  whether states with an older, more established craft-brewing scene have
  better historical OBDB coverage (tested via a researched "pioneer state"
  classification, e.g. CA 1976/New Albion, CO 1979/Boulder Beer — result:
  a clean null, Hedges' g=0.13, effect size near zero, not just
  underpowered). OSM's `start_date` tag was checked as a possible objective
  "brewery age" proxy and found too sparse to use (under 1.4% mean coverage
  across the 13 states). Net effect: state identity dominates capture rate
  for reasons this project hasn't been able to pin down — a genuinely open
  question, not a swept-under-the-rug one.
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

- **County-level brewery density is genuinely, strongly spatially clustered
  — not just a collection of independent local outliers.** Every ranking in
  this project treats counties as statistically independent, so this was
  checked directly (`build_spatial_hotspots.py`): global Moran's I = 0.360
  (p<0.0001 against 9,999 permutations) rejects spatial randomness outright.
  Local Getis-Ord Gi\* (Queen contiguity, Benjamini-Hochberg FDR-corrected at
  q<0.05) finds 217 significant hot-spot counties that collapse into five
  real, named regions rather than scattering: the Colorado Front
  Range/Rockies (64 counties, spilling into MT/WY/ID), the Pacific Northwest
  (52, OR/WA/CA), New England (50, ME/VT/NH/MA/NY), and two Michigan
  clusters (16 in the north, 11 in the southwest around Kalamazoo/Grand
  Rapids). The 5 cold spots are exactly what you'd expect — dense urban
  cores with low per-capita counts (Manhattan, the Bronx, Bergen NJ, Hudson
  NJ, Fulton GA). The clustering survives the capture-rate correction
  (Moran's I = 0.299, 96% label agreement), so it isn't an artifact of
  OBDB's uneven state-level coverage.
- **A spatially-aware model built on that finding genuinely outperforms the
  existing shrinkage model, not just marginally.** Both of this project's
  models treat counties as independent, discarding the confirmed neighbor
  correlation above — `fit_spatial_car_model.py` builds a Bayesian
  Negative-Binomial ICAR (conditional autoregressive) model that instead
  smooths each county's rate toward its geographic neighbors. On a held-out
  80/20 split, it generalizes better than the existing empirical-Bayes
  model (mean log-likelihood −1.126/county vs. −1.253/county), and — the
  real validation — it moves counties in exactly the direction the
  independently-confirmed hot/cold spots predict: confirmed hot-spot
  counties move up in rank by a mean of +52.6 (75% moved up), confirmed
  cold-spot counties move down by a mean of −68.4, while everything else
  stays roughly flat (mean −6). Overall correlation with the existing
  ranking stays high (Spearman ρ=0.86) — this is a targeted correction
  where the spatial signal says it should apply, not a wholesale reshuffle.
  Not yet adopted as the project's default ranking; flagged as the
  highest-leverage next accuracy improvement since it needs no new data,
  just a different prior.
- **The inverse ranking — large-population counties with unexpectedly *low*
  density — surfaces a different, coherent story than "random leftover
  counties."** (`build_brewery_deserts.py`,
  `data/processed/us_county_brewery_deserts.csv`.) The bottom of the
  corrected-shrunken ranking (population ≥ 50,000) is dominated by large
  suburban/exurban counties sitting right next to metros with thriving
  brewery scenes — Gwinnett, Cherokee, and Clayton Counties outside Atlanta;
  Fort Bend County outside Houston; Passaic and Hudson Counties outside New
  York City; Osceola County outside Orlando — rather than remote rural
  counties, suggesting breweries cluster into urban cores and gentrifying
  neighborhoods and skip nearby large-population suburbs even when the
  underlying metro clearly supports the category. Raw and corrected rankings
  agree closely on this list (Spearman ρ=0.976) — unlike the high-density
  ranking, the correction model doesn't reshuffle who counts as a "desert."
- **A state-level rollup** (`build_state_rollup_table.py`,
  `data/processed/state_rollup_table.csv`) makes a structural property of
  the correction model visible that wasn't obvious from county-level output
  alone: **the 23 directly-calibrated states show far more extreme and more
  variable rank movement under correction (SD 60.4 ranks, range −109 to
  +80) than the 28 pooled-estimate states (SD 14.2, range −15 to +48) —
  not because pooled states truly need smaller corrections, but because the
  pooled regression can only produce capture rates in a narrow band (~0.50–
  0.80) by construction, while real measured capture rates range from 0.465
  (VA) to a clipped 1.0 (TX, IL, WV, WY, MO).** In other words, the
  correction's effect on the 28 uncalibrated states is systematically muted
  relative to what direct measurement would likely show — and this gap
  widened, not narrowed, as more states were calibrated this round: the
  pooled-vs-calibrated SD ratio went from ~3.2x (13 states) to ~4.3x (23
  states). Separately, CA, PA, VA, NY, and NC have the largest absolute
  raw-vs-corrected brewery-count gaps (CA: 763→1,272 corrected, +509) —
  where OBDB is believed to undercount most in raw brewery-count terms.
- **Adding county-level population growth as a Model B covariate is a clean
  null.** (5-year ACS vintage comparison, 2019→2024.) Coefficient =
  0.000895, p=0.853 — no detectable relationship between recent population
  growth and brewery count once income, age, college share, tourism, and
  state fixed effects are already in the model. The top-20 "more breweries
  than expected" list is essentially unchanged (two adjacent-rank swaps
  only). Reported here on the same terms as the capture-rate-driver test
  above: an honest null, not a result worth hiding.
- **A three-panel side-by-side comparison figure**
  (`build_map_comparison.py`, `data/processed/us_brewery_density_comparison.png`)
  makes the capture-rate correction's uneven effect (see above) visible in
  one image rather than requiring cross-reference between separate PNGs —
  the Northeast/Southeast corridor visibly darkens by one to two color bins
  from the shrunken to the corrected panel (Allegheny County PA: 3.42→7.04
  per 100k; Fulton County GA: 3.43→7.23) while Texas counties stay flat
  (Harris, Dallas, Tarrant, Bexar all move by <0.01).
- **Choropleth county labels are now collision-aware and data-driven, not a
  fixed hand-picked list** (`src/breweries/map_labels.py`). The original 8
  face-validity anchor cities (Boulder, Bend, Asheville, ...) are still
  placed first and always win contested space, but up to 22 additional
  labels are now generated from the actual top-rate counties on each map
  and placed via real text-bounding-box collision detection (12 candidate
  offset positions tried per label, skipped silently if none avoid a
  collision with an already-placed label, the legend, or a marker dot) —
  so a genuinely dark county (e.g. Skagit County WA, Coconino County AZ,
  Natrona County WY on the corrected map) gets labeled even when it wasn't
  anticipated in advance, without cluttering the plot or overlapping other
  elements.

## Known limitations and possible next steps

- **28 states were investigated across two rounds and found to have no
  usable bulk brewery/alcohol-license open-data source.** First round —
  Mississippi, Ohio, Vermont, and Minnesota have no usable source at all
  (checked thoroughly — no state open-data portal, only an interactive
  per-record search tool this project's rules don't permit scripting
  around); Tennessee, Arizona, and South Carolina are structurally
  different: Tennessee's ABC doesn't regulate ordinary beer at all (it's
  licensed locally, city-by-city, with no state roll-up), Arizona's and
  South Carolina's licensing agencies have no bulk export, only
  session-gated interactive lookups — all three still got a 3-source
  (OBDB/OSM/CBP) county dataset used for face-validity checks, just not
  folded into the correction model, which needs the 4th independent leg to
  mean anything. Second, broader round — every other state not yet
  calibrated (AL, AK, AR, DE, HI, ID, IN, IA, KS, LA, ME, MD, MT, NV, NH,
  NM, ND, OK, RI, SD, UT) was checked and excluded for reasons that varied
  meaningfully rather than being one interchangeable "no data" bucket:
  bot/WAF-protected sites (AK), decommissioned or nonfunctional open-data
  portals (AR, ME), fragmented county-level licensing with no state
  roll-up (HI, MD — the same structural gap as Tennessee), a genuine bulk
  source that lacked the license-type field needed for rigorous inclusion
  (NV's combined manufacturer list has no way to distinguish breweries from
  wineries/distilleries by an official field), and — most commonly —
  interactive-only search tools or login-gated portals with no export.
  Full per-state reasoning: `docs/methods_memo.md` Section 8.
- **The OBDB/OSM union candidate checker (`build_obdb_osm_union.py`) is
  meaningfully better after four rounds of fixes but still has a nonzero,
  measured false-positive rate.** Rounds 1-2: missing OBDB geocoding before
  matching, incomplete name-suffix stripping, greedy 1:1 record matching
  replaced with an optimal bipartite assignment
  (`scipy.optimize.linear_sum_assignment`), and compound-name handling
  (`"X / Y Alehouse"`-style OBDB records matching against each
  `/`-delimited sub-name independently). Round 3 (fresh n=28 manual
  spot-check after rounds 1-2): found the two targeted fixes worked, but a
  new ~14% (4/28) false-positive rate from three *different* causes —
  OBDB/OSM coordinate mismatches beyond the 300m match radius (e.g.
  "Cellarmaker," San Francisco: OBDB's geocoded point sits 3.6km from OSM's
  actual node), near-threshold name/abbreviation renames just under the
  match-score cutoff, and OBDB "planning"-status records with no street
  address that can never be geocoded at all (a structurally different,
  unfixable-by-matching-logic issue). Round 4: added an opt-in two-stage
  fallback to `match_records()` — a tight-distance/loose-name pass (150m,
  score≥55) for near-threshold renames, and a loose-distance/tight-name pass
  (5,000m, score≥90) for coordinate mismatches — validated to fix the
  Cellarmaker case and a real near-threshold rename ("Wild Heaven Beer" vs.
  OBDB's "Wild Heaven Craft Beers") without reintroducing any previously-
  fixed false match. This brought the national "genuinely absent" count from
  3,899 → 3,374 → 3,261 → **2,829 (40.8% more breweries than OBDB alone,
  down from 47.0%)**. A fresh n=25 spot-check after round 4 found only 1
  residual false positive from the two targeted classes (a brewery whose
  OBDB/OSM coordinates are 9.5km apart, beyond even the widened 5km cap —
  the cap's conservatism was directly confirmed by two cases where a
  same-name pair correctly stayed *unmatched* because the two locations were
  genuinely ~19-22km apart, i.e. actually different branches). The
  no-street-address class (round 3, cause 3) remains unaddressed — those
  records structurally cannot be geocoded by this pipeline regardless of
  matching logic. Given this residual rate, still treat the tool's output as
  candidates for review, not a validated correction.
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
  the top of this file), with a searchable/sortable Table tab alongside the
  map for finding a specific county or metro by name — but still only at
  county and CBSA level; a place-level toggle would round it out further.
- **No automated CI** — the 80-test pytest suite (`tests/`) exists and
  passes but isn't wired into a CI pipeline; a bug could still land on `main`
  without the suite being run.

## Codebase audit

This codebase went through a 4-agent parallel correctness audit (statistical
modeling, data-source pipeline, build/analysis scripts, documentation), a
follow-up manual review, a second round of agent-driven work (6 parallel
agents: 3 new calibration states, a pytest suite, LOSO validation, CBSA/place
tables, plus a multi-source capture model investigation and an OBDB/OSM
union checker), a third round adding 4 more calibration states (IL, CA, NY,
VA) out of 10 investigated, a fourth round (4 parallel agents) applying
the correction model at national scale for the first time, adding a
calibration-confidence layer to the interactive map, testing what drives
state-level capture-rate variation, and further refining the union checker's
matching algorithm, a fifth round (6 parallel agents plus collision-aware
choropleth labeling done directly) adding a spatial hot-spot clustering
analysis, the inverse "brewery deserts" ranking, a state-level rollup
table, a three-panel raw/shrunken/corrected comparison figure, a
population-growth Model B covariate (tested, clean null), and a fourth
round of union-checker matching fixes, and a sixth round adding a
searchable/sortable table view to the interactive map, a Bayesian spatial
(ICAR) alternative to the flat-mean shrinkage prior, and 10 more calibration
states/DC (KY, FL, CT, MA, MO, NE, NJ, WV, WY, DC) found via a systematic
second-pass investigation of every remaining state — bringing the total to
23. That last piece surfaced a real lesson about this project's own
process, not just the pipeline: several of the calibration agents'
self-reported capture-rate percentages didn't match their own saved
data (most notably Missouri, self-reported as a normal ~54% state but
actually 166% — an inverted ratio — and Massachusetts, self-reported as a
clipped >100% state but actually a normal 83%), caught only because the
central model refit — deliberately kept as a single, non-parallelized step
specifically to avoid this class of error — reproduced all 13 pre-existing
states' values almost exactly, giving a trustworthy baseline against which
the new states' numbers could be checked rather than taken on faith. Real
bugs caught across all six rounds:

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
  TIGER `NAMELSAD` for every state, rather than picking one convention. The
  same bare-`NAME`-vs-`NAMELSAD` distinction resurfaced when adding
  data-driven choropleth labels (`map_labels.py`) — auto-generated labels
  now use `NAMELSAD` for the same reason.
- Comparing two ACS 5-year vintages (for the population-growth covariate)
  surfaced a cross-vintage geography mismatch, not a code bug: Connecticut
  switched from county-equivalents to "Planning Regions" as its official
  Census geography after 2019, and two Alaska census areas were split out of
  the former Valdez-Cordova Census Area around the same time — so 11
  counties/regions have no earlier-vintage population value and are
  correctly dropped (as `NaN`, not silently zeroed) from that covariate
  rather than mismatched to the wrong geography.
- The interactive map had no `<meta name="viewport">` tag at all — mobile
  browsers default to rendering at a ~980px virtual layout width and
  scaling the whole page down to fit the physical screen when this is
  missing, which also meant every `max-width` mobile media query in the
  stylesheet was silently inert on a real phone regardless of screen size.
  Found from a user-reported screenshot of the map looking "weird" on
  mobile — the header's four toggle-groups were wrapping onto five stacked
  full-width rows, squeezing the map itself down to a sliver at the bottom.
  Fixed by adding the meta tag and reworking the mobile layout (title
  stacked above a single horizontally-scrollable control strip). A second,
  related bug surfaced while verifying the fix: the scrollable control
  strip's container wasn't actually containing its own overflow (a classic
  nested-flexbox gap — a flex item with `overflow-x:auto` still needs an
  explicit width constraint from its container to engage internal
  scrolling instead of just growing past it), which was silently pushing
  the entire page ~250px wider than the viewport. Caught by measuring
  `document.body.scrollWidth` directly rather than trusting a visual
  screenshot alone, since local headless-Chrome testing turned out to have
  its own unrelated viewport-size quirk (a ~500px floor that ignored the
  requested window size) that could easily have been mistaken for the same
  bug or masked it entirely.
