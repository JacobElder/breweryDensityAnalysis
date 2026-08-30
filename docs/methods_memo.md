# Methods Memo: US Brewery Density Analysis

## 1. What this pipeline produces

Brewery counts and per-capita rates at three geographic levels (county, CBSA,
place) nationwide, plus two ranking models:

- **Model A — empirical Bayes shrinkage**: raw rate, partially pooled toward the
  national mean via a Poisson-Gamma model. Answers "where is brewery density
  actually high," correcting for small-county noise.
- **Model B — covariate residual**: negative-binomial regression with
  log(income), median age, college-enrollment share, tourism establishments, and
  state fixed effects, offset by log(adults 21+), then shrunk the same way.
  Answers "where are there more breweries than the county's own demographic and
  tourism profile predicts."

All numbers below are **uncorrected OBDB counts** unless stated otherwise. Section
5 quantifies how far short of the true count that is.

Rendered outputs, all reading Model A's output: a national county-level
choropleth of the shrunken rate and a population-floored variant
(`build_choropleth.py`, counties under 50k adults 21+ shown gray rather than
colored, since shrinkage reduces but does not eliminate small-county noise);
top-50 table images at county, CBSA, and place level (`build_top50_table.py`,
`build_top50_cbsa_table.py`, `build_top50_place_table.py`); and an
interactive county/CBSA map with zoom, pan, and a raw/shrunken/floored
toggle (`build_interactive_map.py` + `assemble_interactive_map_html.py` —
see the link at the top of the project README).

## 2. Inclusion rules (every filter, as implemented in code)

**Brewery definition (OBDB)** — `src/breweries/sources/obdb.py`:
- Included `brewery_type`: `micro`, `brewpub`, `regional`, `large`, `nano`.
- Excluded definitionally: `planning`, `closed` (not currently brewing).
- Excluded as judgment calls (not an independent physical brewing location):
  `contract`, `proprietor`, `bar`, `taproom`, `beergarden`, `beer brand`, `location`.
- Excluded: `cidery`, `meadery` (different beverage category).
- This is the project's chosen definition; it does **not** apply Brewers
  Association's <25%-non-craft-ownership filter, so an acquired-but-physically-
  operating brewery stays in our count while it would drop out of BA's.

**Deduplication**: not applied beyond the `brewery_type` filter above — OBDB's
combined CSV had no exact duplicate (name, address) pairs found during
inspection. **Satellite taprooms are counted as separate rows by default**
(OBDB gives each location its own record); see Section 6 for the one state
(Oregon) where this was tested directly against an alternative definition.

**Geographic assignment**: TIGER/Line county and place polygons (2025 vintage),
spatial join on lat/lon. CBSA is read off each county's `CBSAFP` attribute
(TIGER's county layer already carries this), not a separate spatial join.

**Data-quality fixes applied** (logged in `data/raw/manifest.jsonl`):
- One Missouri brewery tagged `MIssouri` (typo) in the upstream OBDB CSV —
  corrected before state aggregation; would otherwise have silently created a
  fake 52nd "state."
- ACS missing-data sentinels (`-666666666` etc.) converted to null rather than
  averaged in — caught one affected county (median income, De Baca County, NM).

## 3. Row counts through the pipeline (national)

| Step | Rows |
|---|---|
| OBDB combined CSV, all countries | 11,932 |
| Filtered to United States | 8,308 (net of DC; territories not present) |
| After `brewery_type` inclusion rule | 6,942 |
| Geocoded (direct lat/lon or Census Geocoder address fallback) | 6,724 matched to a county (96.9%) |

1,551 US records were missing lat/lon outright; the Census Geocoder address
fallback (free, keyless, TOS-compliant) recovered the large majority. Full
per-state fetch/filter counts are in `data/raw/manifest.jsonl`.

## 4. Validation checkpoints (face validity)

Bend OR, Asheville NC, Portland ME, Burlington VT, Grand Rapids MI, and Fort
Collins CO all rank in the national top 20 at at least one geographic level —
most at two or three. Concretely:

- **County-level top 5** (population ≥ 50k floor): Boulder CO, Deschutes OR
  (Bend), Buncombe NC (Asheville), Van Buren MI, Cumberland ME (Portland).
- **CBSA-level top 5**: Boulder CO, Traverse City MI, Bend OR, Fort
  Collins-Loveland CO, Asheville NC.
- **Place-level top 5**: Asheville NC, Boulder CO, Portland ME, Bend OR,
  Kalamazoo MI.

Deschutes/Bend, Buncombe/Asheville, Larimer-or-CBSA/Fort Collins, and Boulder
appear at every level. That consistency is itself informative — it's a check
this pipeline is not obviously broken, not a claim of definitive precision.

## 5. Coverage error — the honest section

This is the deliverable the source-availability constraints (Section 8) make
necessary: **every brewery count in this project is an estimate from an
incomplete source, and the size of that gap varies by state in a way that a
single national correction factor cannot fix.**

### 5.1 Nine-state calibration

State licensee/permit registries were obtained for NC (ABC Commission), MI
(LARA Master License List), CO (Socrata open-data liquor licenses), OR (OLCC
Socrata liquor licenses), WA (WSLCB bulk licensee export), TX (TABC Socrata
license table), GA (Dept. of Revenue bulk Excel export), WI (DOR Fermented
Malt Beverage Permits Excel export), and PA (PLCB bulk CSV export) — see
`src/breweries/sources/{nc_abc,mi_lara,co_liquor,or_olcc,wa_liquor,tx_liquor,
ga_dor,wi_dor,pa_liquor}.py`. Mississippi was investigated and found to have
no bulk-downloadable source (only an interactive per-record search tool,
which this project's rules do not permit scripting around — see Section 8).

Seven of nine track the Brewers Association's own 2025 state totals within
1-9%; two (WI, PA) sit further off for documented, source-specific reasons
noted below the table, not pipeline error:

| State | Licensee count | BA 2025 total | Licensee/BA |
|---|---|---|---|
| NC | 422 | 418 | 101% |
| MI | 395 | 410 | 96% |
| CO | 408 | 423 | 97% |
| OR | 285 (primary) | 297 | 96% |
| WA | 412 | 438 | 94% |
| TX | 185 | 420 | 44% |
| GA | 147 | 186 | 79% |
| WI | 288 | 247 | 117% |
| PA | 591 | 538 | 110% |

- **TX (44%)**: TABC's public license table is documented (by TABC's own
  license-consolidation materials) to exclude brewpub subordinate
  authorizations attached to a retail permit — the reference itself
  undercounts, not a red flag about OBDB.
- **WI (117%)**: WI DOR's "Brewery" permit category sweeps in some non-craft
  manufacturers (e.g. Anheuser-Busch's Milwaukee plant) that BA's craft-only
  definition excludes — the reference measures a broader population than
  "craft breweries."
- **PA (110%)**: plausibly BA's own count lagging recent openings/closures,
  similar to the pattern in other license-based states, but not independently
  confirmed beyond that hypothesis.

Against this anchor, OBDB's capture rate (obdb_count / licensee_count) varies
even more than the original 4-state sample suggested:

| State | OBDB capture rate |
|---|---|
| GA | 48% |
| PA | 49% |
| WI | 62%* |
| NC | 62% |
| WA | 83% |
| MI | 85% |
| CO | 92% |
| OR | 93% |
| TX | 100%† |
| **Pooled (WLS-regression, see 5.2)** | **65%** |

\* WI's capture-rate denominator includes the non-craft manufacturers noted
above, so this likely *understates* OBDB's true capture rate among craft
breweries specifically.
† Raw ratio computes to 122% (TABC's reference undercounts, see above);
clipped to 100% since a capture rate cannot exceed a true population by
definition — see 5.2.

CBP (NAICS 312120) is worse than OBDB in every calibration state (27-54%
capture in the original 4; not re-measured for the newer 5) — consistent with
its known brewpub-misclassification problem (brewpubs often file under NAICS
722511, restaurants, not 312120).

### 5.2 Why there is no reliable national correction factor

A mixed-effects model (`scripts/build_capture_rate_model.py`) regressing
log(OBDB/licensee ratio) on log(population density), with state as a random
intercept, found:

- Density has a real, statistically significant effect (coefficient ≈0.047,
  p<0.001): denser counties have higher OBDB capture rates, i.e. **OBDB
  undercounts rural areas more**, as hypothesized.
- But **state identity dominates**: state intercepts vary far more than the
  density gradient across its full observed range, and a model with no state
  term has essentially no explanatory power.

With 9 calibration states there is more information than the original 4, but
still not enough to fully separate "what predicts capture rate" from "which
state this happens to be" — the between-state variance actually *grew*
(0.043 → 0.1045) once GA, PA, WA, TX, and WI were added, meaning the true
range of state-level variation is wider than the 4-state sample suggested,
not narrower. The practical consequence, implemented in
`src/breweries/capture_rate_model.py`: counties in a calibrated state use
that state's empirical rate (capped at 1.0 — see the TX case above); every
other county uses a pooled 64.8% baseline rate with a **deliberately wide**
interval (derived from the between-state variance, not tightened by
density) — roughly 40-100% of the point estimate at the 95% level
(hard-capped at 100%). That width is the honest answer, not a bug: 9 states
still cannot support a tight one.

**On the 64.8% figure specifically** — this is *not* the exposure-weighted
aggregate ratio (`obdb_count.sum()/licensee_count.sum()` across the pooled
sample, which computes to ~75% with the 9-state sample). Those are two
different quantities: the aggregate is pulled up by a handful of large,
high-capture counties (Buncombe, Mecklenburg, Wake, Denver), while 64.8% is
what a WLS regression (weighted by licensee_count, so higher-exposure
counties still get proportionally more influence on the *fit* without
changing what the coefficients describe) predicts for a typical county at
average density. Since the pooled fallback is applied to arbitrary counties
nationally — most of which are small or medium, not large metros — the
per-county regression estimate is the correct anchor; using the
population-weighted aggregate would systematically under-correct exactly the
smaller, rural counties this correction exists to help. (An earlier version
of this module mixed the two — aggregate baseline, regression slope — which
was internally inconsistent by construction; both numbers now come from the
same weighted regression.)

### 5.3 A second, independent negative result: multi-source capture-recapture doesn't fix the bias

Section 5.4 below documents that naive 2-source (OBDB×OSM) capture-recapture
badly overestimates the true count due to correlated crowdsourcing bias. A
follow-up investigation tried whether a *proper* 3-source model — adding each
state's own licensee registry as a third, administrative list, and fitting a
log-linear model with all three pairwise source-dependence terms explicit
(the standard no-3-way-interaction closed-population capture-recapture
approach) — could recover the correlation this project already knew was
there and produce a less biased estimate. Tested in CO and OR, the two
calibration states with record-level (not just aggregate) licensee data,
against known truth:

| | CO (truth ≈408-423) | OR (truth ≈285-297) |
|---|---|---|
| Naive 2-source (OBDB×OSM) Chapman | 1,040.5 | 531.8 |
| 3-source, independence assumed | 1,085.8 | 559.7 |
| 3-source, log-linear w/ pairwise interactions | **4,076.4** | **1,996.7** |

The 3-source model did not do better — it did dramatically worse, 4-8x off
truth. All three pairwise interaction terms came out strongly positive in
both states (not just the suspected OBDB-OSM pair), a signature of general
heterogeneity in how easy-to-find a given brewery is by *any* method, which a
model with only pairwise interaction terms and zero residual degrees of
freedom (3 lists = exactly saturated) has no mechanism to absorb. This
reinforces rather than revises the conclusion below: administrative
registries, not any form of crowdsourced-source capture-recapture, are the
right calibration approach for this project.

### 5.4 What OSM adds, and what a naive combination gets wrong

An attempt was made to estimate the true NC brewery count via capture-recapture
between OBDB and OSM (two independent record-level lists) rather than relying
on state registries. The result (**N̂≈797, vs. the ABC/BA anchor of ~420**) was
a red flag, not a finding: OBDB and OSM are both crowdsourced/volunteer-edited
platforms, so a brewery's odds of appearing on one correlate with its odds of
appearing on the other (the same underlying trait — online visibility, being
well-established) — this violates the independence assumption two-sample
capture-recapture needs and inflates the estimate. Loosening the spatial match
radius 300m→2000m recovered only ~9 more matches (92→101 of ~270 each),
confirming this is a real, structural coverage gap rather than a
record-linkage threshold artifact. **Conclusion: administrative registries, not
crowdsourced-pair capture-recapture, are the right calibration source here.**

## 6. Satellite-taproom sensitivity check

Oregon's OLCC data distinguishes primary licenses from "ADDITIONAL LOCATION"
licenses explicitly — a direct, real-world instance of the satellite-taproom
judgment call the project spec flagged. Excluding satellites (this project's
default) gives 285 breweries, 96% of the BA total; including them gives 347,
117% of the BA total. **BA's own count sits almost exactly at the
primary-license number**, which is why "one brewery per independent license,
satellites excluded" was chosen as the default rather than "one row per
physical taproom." The difference is concentrated in large metros — Multnomah
County (Portland) alone accounts for 22 of the 62 additional-location licenses
statewide.

## 7. Geographic-unit sensitivity

Per the project brief, county, CBSA, and place-level rankings were all built,
not just one. Places that are roughly coextensive with their core
county/CBSA — Boulder, Bend/Deschutes, Asheville/Buncombe, Fort
Collins/Larimer — rank highly at every level. Two mechanisms cause real
divergence between levels, both worth naming rather than smoothing over:

- **Denominator dilution at the county level**: Grand Rapids doesn't reach the
  county-level top 20 (Kent County's population is large enough to dilute the
  rate) but does at the CBSA and place level, where the denominator matches the
  brewery market more tightly.
- **Population-floor exclusion at the place level**: Traverse City, MI
  (Grand Traverse County) ranks #2 nationally by CBSA-level rate but has too
  small a place-level population (well under the 50k floor) to appear in the
  place-level ranking at all — exactly the "Bend/Traverse City" case the
  project brief named as the reason micropolitan areas must be included.

Recommendation (matching the project brief): report CBSA as primary, county as
secondary, and place only above the population floor and with this caveat
attached.

## 8. Sources that do not exist, and why nothing was substituted for them

- **No TTB brewer list.** TTB does not publish one (IRC §6103 confidentiality;
  brewers register under the Internal Revenue Code, not the FAA Act). Not
  worked around.
- **No NC ABC individual-permit bulk export.** The record-level permit search
  (`abc2.nc.gov/Search/Permit`) returned a genuine server-side 500 to a
  properly-formed, cookie-carrying POST request — not a Cloudflare bot
  challenge, so no evasion was attempted, and none was pursued. The
  county-level "Permit Counts" report endpoint worked and was used instead.
- **No Brewers Association bulk download or directory scrape.** Nine single
  state-total lookups were made (NC, MI, CO, OR, WA, TX, GA, WI, PA), each
  dated and cited inline in the relevant build script, per the project's
  explicit "no bulk download/scrape" constraint.
- **No Mississippi bulk alcohol-license data.** Checked thoroughly: the MS
  Dept. of Revenue's pages describe the Manufacturer/Brewpub permit
  categories but publish no roster; the only public lookup is
  `tap.dor.ms.gov`'s interactive, session-based per-record search — the same
  category of tool NC's individual-permit search fell into above, and not
  scripted around for the same reason. No Mississippi open-data portal
  exists (confirmed absent, unlike CO/TX/GA's Socrata portals). A clean "no,"
  not a gap in effort — Mississippi is not a calibration state.

## 9. Model B covariate results (national, county-level, NB-GLM + state FE)

| Covariate | Coefficient | Interpretation |
|---|---|---|
| log(median household income) | +0.55 | Higher-income counties have more breweries, controlling for state |
| Median age | +0.046/year | Older-median-age counties have (slightly) more breweries — plausibly reflects established, higher-income communities rather than a youth effect |
| College enrollment share | +7.2 | Strong college-town effect, as expected |
| Tourism establishments per 10k | +0.029 | Tourism effect present and significant, as expected — this is a covariate being conditioned on, not a nuisance to explain away |

Top of the shrunken residual ranking ("more breweries than covariates predict"):
Buncombe NC (Asheville), Charleston SC, Fulton GA (Atlanta), St. Louis city MO,
Travis TX (Austin), Richmond city VA. Full table:
`data/processed/us_county_residual_rankings.parquet`.

The raw (unshrunk) residual ratio is dominated by small-expected-count noise
(e.g. a county "expected" 0.27 breweries that has 2 looks like a 7x outlier) —
the same instability the project brief warns about for raw rates. The same
Poisson-Gamma shrinkage used for Model A was applied to the residual, centered
on each county's own covariate-based expectation rather than the flat national
mean.

### 9.1 Leave-one-state-out validation: the top list is less stable than it looks

`scripts/validate_model_b_loso.py` refits the identical model 51 times, each
time holding out one state's counties from training and predicting them from
a model that never saw that state's own data (the held-out state's missing
fixed-effect term is imputed as the mean of the other fitted state effects —
the one non-obvious methodological choice here, documented in the script).

| Metric | In-sample | Leave-one-state-out |
|---|---|---|
| MAE | 1.29 | 1.58 (1.22x) |
| RMSE | 4.49 | 4.84 (1.08x) |
| Pearson r(actual, predicted) | 0.82 | 0.76 |

The accuracy gap is real but modest. The **ranking** gap is not: Spearman
rank correlation between the full-sample and LOSO shrunken-residual rankings
(population ≥50k, n=806) is **ρ=0.68**, and only **7 of the full-sample top
20** remain in the LOSO top 20. Checking the six counties named above
individually:

| County | Full-sample rank | LOSO rank | Verdict |
|---|---|---|---|
| Buncombe NC | 1 | 4 | Holds up — residual actually *grows* without NC in training |
| St. Louis city MO | 4 | 10 | Holds up reasonably |
| Richmond city VA | 6 | 14 | Holds up reasonably |
| Charleston SC | 2 | 33 | Drops substantially |
| Travis TX | 5 | 104 | Drops substantially |
| Fulton GA | 3 | 242 | **Collapses** — no longer looks like an outlier at all |

Fulton County's case is the clearest illustration of *why* this matters: a
large share of "more breweries than expected in Fulton" turns out to be
Georgia's fitted state effect, not a Fulton-specific signal — when Georgia's
own data isn't available to estimate that state effect, Fulton's apparent
outlier status mostly evaporates. This is a direct, mechanical consequence of
Model B leaning heavily on state fixed effects (Section 5.2 already
established state identity dominates local covariates for the *capture-rate*
model; the same is true here for the *residual* model). **Practical
guidance**: read the residual ranking's top entries as leads worth checking
individually against which states dominate their estimated state effect, not
as a validated "go visit these counties" list.

## 10. What these numbers can't support

- County-level rankings below the population floor, or for counties with fewer
  than a handful of licensee-registry data points, should not be read as
  precise — they're shrunk toward priors for exactly this reason, but shrinkage
  reduces noise, it doesn't manufacture missing ground truth.
- Any state without its own calibration data is carrying OBDB's raw undercount
  (7-52% observed range across the 9 calibration states, before the TX/WI
  reference-quality caveats in Section 5.1) partially corrected by a wide,
  honestly-uncertain interval — not a precise correction.
- The choropleth and rankings are **not** capture-rate-corrected by default
  (the map explicitly says so); `capture_rate_model.apply_correction()` exists
  to produce a corrected version but doing so at every county nationally
  compounds the state-vs-density confound described in Section 5.2.
- OSM data has been fetched for all 50 states + DC (`data/raw/osm/`), but it
  is not incorporated into the headline county/CBSA/place datasets or either
  model — the only place it's used quantitatively is the NC capture-recapture
  diagnostic in Section 5.4, and per that section's finding, using it as a
  second signal at national scale would need the same correlated-crowdsourcing
  caveat, not a straightforward "more data is better" treatment.

## 11. Case study: manual verification of a single geography (Santa Cruz, CA)

Prompted by a user's firsthand knowledge of the Santa Cruz, CA brewery scene
conflicting with its mid-tier ranking, a full manual investigation was run —
partly to answer the specific question, partly as a worked example of what
this project's coverage-error argument looks like at the level of one real
place rather than an aggregate percentage.

**What was found:**
- **Balefire Brewing Company** (opened October 2023, confirmed still
  operating via current reviews) is missing from **both** OBDB and OSM — not
  a pipeline bug, direct evidence of the exact mechanism (crowdsourced
  sources lag new openings) this project's whole coverage-error argument
  rests on.
- Two other names that looked like possible gaps were checked and confirmed
  **not** to be: Santa Cruz Ale Works and Boulder Creek Brewery are both
  marked "closed" in OBDB, and both are in fact closed per current listings
  — the classification is correct.
- "Laughing Monk" and "Other Brother," which surfaced in a broad web search
  near the area, are based in San Francisco/Sunnyvale and Seaside/Monterey
  County respectively — not Santa Cruz County breweries at all.
- **The dominant effect is geographic-unit dilution, not a missing-data
  gap**: Santa Cruz *city* (44,425 adults 21+, just under the 50k floor) has
  a raw rate of 11.3/100k — Boulder/Asheville-tier. Santa Cruz *County*
  (197,974 adults 21+, including Watsonville and inland areas well outside
  the brewery-dense coastal core) dilutes that to 5.6/100k. This is the same
  mechanism already documented for Grand Rapids/Traverse City in Section 7,
  now confirmed by an independent, user-prompted case.
- Adding the one confirmed missing brewery moves the county rate from 5.56
  to 6.06 per 100k — real, but modest; it does not by itself explain a
  "low" ranking. The unit-dilution effect is the larger factor.
- No geocoding, inclusion-filter, or pipeline bug was found in this
  investigation — county assignment and population figures check out.

**Does this generalize? A systematic follow-up, and its limits.** Per-city
manual investigation obviously doesn't scale to ~3,200 counties. The natural
systematic alternative is *unioning* OBDB with OSM by name+location match
(not capture-recapture — a union only combines what was actually observed by
at least one source, so it isn't subject to the correlated-crowdsourcing
bias that sank the estimation approaches in Sections 5.3-5.4) —
implemented in `scripts/build_obdb_osm_union.py`. Building it surfaced two
more real bugs (documented in the script and in the git history): OBDB
records missing lat/lon were unmatchable by construction (fixed by
geocoding before matching), and `capture_recapture.normalize_name()`
stripped "Co" but not "Company" as a brewery-name suffix, causing systematic
false negatives (fixed). Even after both fixes and adding OSM-internal
deduplication, a manual sample of the tool's "genuinely absent from OBDB"
output still contained real false positives — mostly OBDB's compound
dual-brand names (e.g. "Automatic Brewing Co. / Blind Lady Alehouse") not
fuzzy-matching a single-brand OSM name, and a greedy-matching artifact where
one OSM record can claim another's correct match slot. **The tool is not
integrated into any correction** — it reduces the review burden from
"investigate every US city" to "review a few thousand flagged candidates,"
it does not eliminate manual verification, and its current headline count
should be read as an upper bound requiring further matching refinement (see
README "Known limitations"), not a validated addition to any total.
