# Methods Memo: US Brewery Density Analysis

## 1. What this pipeline produces

Brewery counts and per-capita rates at three geographic levels (county, CBSA,
place) nationwide, plus two ranking models:

- **Model A, empirical Bayes shrinkage**: raw rate, partially pooled toward the
  national mean via a Poisson-Gamma model. Answers "where is brewery density
  actually high," correcting for small-county noise.
- **Model B, covariate residual**: negative-binomial regression with
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
toggle (`build_interactive_map.py` + `assemble_interactive_map_html.py`; see the link at the top of the project README).

## 2. Inclusion rules (every filter, as implemented in code)

**Brewery definition (OBDB)**: `src/breweries/sources/obdb.py`:
- Included `brewery_type`: `micro`, `brewpub`, `regional`, `large`, `nano`.
- Excluded definitionally: `planning`, `closed` (not currently brewing).
- Excluded as judgment calls (not an independent physical brewing location):
  `contract`, `proprietor`, `bar`, `taproom`, `beergarden`, `beer brand`, `location`.
- Excluded: `cidery`, `meadery` (different beverage category).
- This is the project's chosen definition; it does **not** apply Brewers
  Association's <25%-non-craft-ownership filter, so an acquired-but-physically-
  operating brewery stays in our count while it would drop out of BA's.

**Deduplication**: not applied beyond the `brewery_type` filter above: OBDB's
combined CSV had no exact duplicate (name, address) pairs found during
inspection. **Satellite taprooms are counted as separate rows by default**
(OBDB gives each location its own record); see Section 6 for the one state
(Oregon) where this was tested directly against an alternative definition.

**Geographic assignment**: TIGER/Line county and place polygons (2025 vintage),
spatial join on lat/lon. CBSA is read off each county's `CBSAFP` attribute
(TIGER's county layer already carries this), not a separate spatial join.

**Data-quality fixes applied** (logged in `data/raw/manifest.jsonl`):
- One Missouri brewery tagged `MIssouri` (typo) in the upstream OBDB CSV: corrected before state aggregation; would otherwise have silently created a
  fake 52nd "state."
- ACS missing-data sentinels (`-666666666` etc.) converted to null rather than
  averaged in: caught one affected county (median income, De Baca County, NM).

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
Collins CO all rank in the national top 20 at at least one geographic level, most at two or three. Concretely:

- **County-level top 5** (population ≥ 50k floor): Boulder CO, Deschutes OR
  (Bend), Buncombe NC (Asheville), Van Buren MI, Cumberland ME (Portland).
- **CBSA-level top 5**: Boulder CO, Traverse City MI, Bend OR, Fort
  Collins-Loveland CO, Asheville NC.
- **Place-level top 5**: Asheville NC, Boulder CO, Portland ME, Bend OR,
  Kalamazoo MI.

Deschutes/Bend, Buncombe/Asheville, Larimer-or-CBSA/Fort Collins, and Boulder
appear at every level. That consistency is itself informative: it's a check
this pipeline is not obviously broken, not a claim of definitive precision.

Two more national-ranking cases got direct, independent confirmation during
the state-expansion rounds rather than remaining unverified: Flagstaff
(Coconino County, AZ), a national top-20 per-capita result, was confirmed
by all three independent sources available for Arizona (OBDB=12, OSM=9,
CBP=5, all agreeing there's a real cluster, not a single-source artifact) at
nearly 3x the state's second-place county. Richmond city, VA, flagged by
Model B's residual ranking (Section 9), was confirmed to have a genuinely
outsized raw rate (10.2 per 100k adults 21+) once Virginia's own ABC data
was available, correctly distinct from the much larger, rural Richmond
*County* it shares a bare TIGER name with.

## 5. Coverage error: the honest section

This is the deliverable the source-availability constraints (Section 8) make
necessary: **every brewery count in this project is an estimate from an
incomplete source, and the size of that gap varies by state in a way that a
single national correction factor cannot fix.**

### 5.1 Twenty-three-state calibration

State licensee/permit registries were obtained for NC (ABC Commission), MI
(LARA Master License List), CO (Socrata open-data liquor licenses), OR (OLCC
Socrata liquor licenses), WA (WSLCB bulk licensee export), TX (TABC Socrata
license table), GA (Dept. of Revenue bulk Excel export), WI (DOR Fermented
Malt Beverage Permits Excel export), PA (PLCB bulk CSV export), IL (ILCC
daily bulk CSV export), CA (ABC daily bulk CSV export), NY (SLA Socrata
dataset), and VA (ABC bulk Excel export); see
`src/breweries/sources/{nc_abc,mi_lara,co_liquor,or_olcc,wa_liquor,tx_liquor,
ga_dor,wi_dor,pa_liquor,il_liquor,ca_abc,ny_sla,va_abc}.py`.

A second round added ten more states/DC: KY (ABC BELLE Portal JSON export),
FL (DBPR weekly public-records CSV), CT (Socrata "Liquor Permits" dataset,
credential-prefix filtered), MA (ABCC "Active State Licenses" XLSX,
geocoded), MO (ATC "Primary Alcohol License" Socrata dataset), NE (NLCC
"Active License Roster" Excel export), NJ (ABC monthly wholesale/state-issued
licensee listing, geocoded), WV (ABCA "Resident Brewers" PDF list), WY
(state "Malt Beverage Wholesaler List" PDF), and DC (ABRA opendata.dc.gov
GIS FeatureServer layer); see
`src/breweries/sources/{ky_abc,fl_dbpr,ct_dcp,ma_liquor,mo_liquor,ne_liquor,
nj_liquor,wv_abca,wy_liquor,dc_abra}.py`.

Twenty-eight more states were investigated across both rounds and found to
have no usable path to a calibration-quality registry (see Section 8 for the
full per-state accounting): Mississippi, Ohio, Vermont, and Minnesota have no
bulk-downloadable source at all; Tennessee, Arizona, and South Carolina also
lack one but still contribute a 3-source (OBDB/OSM/CBP) county dataset used
for face-validity spot-checks elsewhere in this memo; and a second, broader
sweep (Alabama, Alaska, Arkansas, Delaware, Hawaii, Idaho, Indiana, Iowa,
Kansas, Louisiana, Maine, Maryland, Montana, Nevada, New Hampshire, New
Mexico, North Dakota, Oklahoma, Rhode Island, South Dakota, Utah) turned up
no usable source in any of them either, for reasons that vary meaningfully
(bot/WAF-protected sites, decommissioned open-data portals, fragmented
county-level-only licensing, a genuine bulk source lacking the license-type
field needed for rigorous inclusion, or, most commonly, interactive-only
search tools with no export).

**A methodological note on this round's own process.** Ten states were
investigated in parallel by five separate agent batches, each self-reporting
a computed capture rate. Before trusting those numbers, a single centralized
refit (`scripts/build_capture_rate_model.py`, deliberately kept as one
non-parallelized step to avoid exactly this class of error) reproduced all
13 pre-existing states' capture rates to within rounding of their established
values: a strong signal the refit methodology itself was sound. Checking
the ten new states' reported numbers against that same refit surfaced real
discrepancies in several agents' self-reported summary lines, not in the
underlying saved data: Missouri was reported as a normal ~54% state but its
own saved data shows 166% (the reported ratio was inverted); Massachusetts
was reported as a clipped >100% state but its own data shows a normal 83%
(same inversion, opposite direction); Florida and Connecticut's reported
figures turned out to be licensee-count-vs-BA-total sanity checks mislabeled
as the actual OBDB capture rate. Kentucky and Wyoming's reported figures were
off by a smaller margin (a handful of counties lost to a land-area-join
filter that only affects the aggregate used for model-fitting, not the
underlying per-county data). Nebraska, New Jersey, West Virginia, and DC
checked out. The corrected figures are what appear in the table below; the
lesson is procedural, not statistical: self-reported summary numbers from
parallel work should be treated as claims to verify against the actual
saved artifact, not facts to relay, even when (especially when) they sound
plausible.

Nine of the original thirteen track the Brewers Association's own 2025 state
totals within 1-9%; four (WI, PA, CA, VA) sit further off for documented,
source-specific reasons noted below the table, not pipeline error. Of the
ten added in the second round, most track BA reasonably closely; Massachusetts
runs somewhat high (117%) and Missouri and Wyoming run low (50%, 57%) for
reasons specific to each source, also noted below:

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
| IL | 175 | 288 | 61% |
| CA | 1,270 | 939 | 135% |
| NY | 495 | 525 | 94% |
| VA | 413 | 344 | 120% |
| KY | 97 | 96 | 101% |
| FL | 329 | 379 | 87% |
| CT | 107 | 121 | 88% |
| MA | 244 | 209 | 117% |
| MO | 65 | 130 | 50% |
| NE | 66 | 65 | 102% |
| NJ | 127 | 159 | 80% |
| WV | 33 | 37 | 89% |
| WY | 28 | 49 | 57% |
| DC | 14 | 11 | 127% |

- **TX (44%)**: TABC's public license table is documented (by TABC's own
  license-consolidation materials) to exclude brewpub subordinate
  authorizations attached to a retail permit: the reference itself
  undercounts, not a red flag about OBDB.
- **WI (117%)**: WI DOR's "Brewery" permit category sweeps in some non-craft
  manufacturers (e.g. Anheuser-Busch's Milwaukee plant) that BA's craft-only
  definition excludes: the reference measures a broader population than
  "craft breweries."
- **PA (110%)**: plausibly BA's own count lagging recent openings/closures,
  similar to the pattern in other license-based states, but not independently
  confirmed beyond that hypothesis.
- **CA (135%)**: ABC's export counts *licenses*, and 216 distinct licensees
  hold more than one Type 01/23/75 license (386 "extra" licenses beyond
  one-per-company, e.g. Firestone Walker holds 17), plus some Type-01
  holders are large non-craft operations (e.g. wineries with an incidental
  beer-manufacturer license) that BA's craft definition excludes.
- **VA (120%)**: ABC's export similarly counts licensed premises rather than
  brands; several operators hold multiple Virginia sites (one holds 5).
- **MO (50%, licensee/BA)**: MO ATC's "Microbrewery" license category
  structurally excludes the state's own large/regional breweries
  (Anheuser-Busch, Boulevard Brewing hold no license in this category),
  which pulls the licensee count well below BA's craft-inclusive total, and, more consequentially, drives the OBDB *capture* rate the opposite
  direction, to 166% (see below), since OBDB counts those breweries and the
  licensee reference doesn't.
- **WY (57%, licensee/BA)**: Wyoming brewers only need a wholesaler license
  if they self-distribute (W.S. 12-4-201); a brewery using a third-party
  distributor never appears on this source at all, so both the licensee/BA
  ratio and the OBDB capture rate run high for the same underlying reason.
- **MA (117%, licensee/BA)**: plausibly the same BA-lag pattern already
  noted for PA, not independently confirmed beyond that hypothesis.

Against this anchor, OBDB's capture rate (obdb_count / licensee_count) keeps
widening as more states are added, not narrowing:

| State | OBDB capture rate |
|---|---|
| VA | 46% |
| GA | 48% |
| KY | 54% |
| PA | 49% |
| CT | 58% |
| CA | 60%* |
| WI | 62%* |
| NC | 62% |
| DC | 64% |
| NY | 66% |
| NJ | 75% |
| NE | 76% |
| FL | 76% |
| MA | 83% |
| WA | 83% |
| MI | 85% |
| CO | 92% |
| OR | 93% |
| WV | 100%‡ |
| IL | 109%† |
| TX | 100%† |
| WY | 129%† |
| MO | 166%† |
| **Pooled (WLS-regression, see 5.2)** | **61%** |

\* CA's and WI's capture-rate denominators both include populations broader
than "craft breweries" (see above), so these numbers likely *understate*
OBDB's true capture rate among craft breweries specifically.
† IL's raw ratio computes to 108.6% (ILCC's cumulative export, imperfectly
deduplicated companion license classes); TX's raw ratio computes to 122%
(TABC's reference undercounts); WY's raw ratio computes to 128.6% (the
reference only captures self-distributing brewers); MO's raw ratio computes
to 166.2% (the reference structurally excludes the state's largest
breweries); see above for each. All clipped to 100% since a capture rate
cannot exceed a true population by definition; see 5.2.
‡ WV's raw ratio computes to exactly 100.0%: at the boundary rather than
clearly over it. ABCA's list is a dated PDF snapshot (~13 months stale as of
this fetch) rather than a live query, so this isn't read as meaningfully
different from the >100% states above.

CBP (NAICS 312120) is worse than OBDB in every calibration state where
directly compared (27-54% capture in the original 4 states): consistent
with its known brewpub-misclassification problem (brewpubs often file under
NAICS 722511, restaurants, not 312120).

**A cross-check worth noting**: South Carolina (no state registry, so not in
the tables above) shows OBDB's Charleston County count (24) matching CBP's
independently-collected federal establishment count (24) exactly: evidence
that OBDB's raw count for at least this specific, previously-flagged county
(see Section 9's residual ranking) is not itself inflated, even though the
county's *residual* ranking is one of the less LOSO-stable ones (Section
9.1). Virginia's data provides a similar direct check for Richmond city,
another flagged county: it genuinely has an outsized raw rate (10.2 per 100k
adults 21+), correctly distinct from the much larger, rural Richmond
*County*: Virginia's independent cities share bare TIGER names with a
same-named county in four cases (Fairfax, Franklin, Richmond, Roanoke),
which required a join-key fix in `build_capture_rate_model.py` (join against
both the bare county name and the full `NAMELSAD` for every state, not just
one convention) to avoid silently dropping 8 Virginia rows from the
correction model entirely.

### 5.2 Why there is no reliable national correction factor

A mixed-effects model (`scripts/build_capture_rate_model.py`) regressing
log(OBDB/licensee ratio) on log(population density), with state as a random
intercept, found:

- Density has a real, statistically significant effect (coefficient ≈0.062,
  p<0.001): denser counties have higher OBDB capture rates, i.e. **OBDB
  undercounts rural areas more**, as hypothesized.
- But **state identity dominates**: state intercepts vary far more than the
  density gradient across its full observed range, and a model with no state
  term has essentially no explanatory power.

With 23 calibration states/DC there is considerably more information than
the original 4, but still not enough to fully separate "what predicts
capture rate" from "which state this happens to be." The between-state
variance has moved around as states were added (0.043 at 4 states → 0.1045
at 9 → 0.0897 at 13 → 0.1062 at 23) rather than converging monotonically,
which is itself informative: the range of state-level variation isn't
settling down yet, so treat the current interval width as a snapshot, not a
converged estimate. The practical consequence, implemented in
`src/breweries/capture_rate_model.py`: counties in a calibrated state use
that state's empirical rate (capped at 1.0; see the TX/IL/WV/WY/MO cases
above); every other county uses a pooled 61.0% baseline rate with a
**deliberately wide** 95% interval: 32.2% to 100% (the uncapped upper
bound is 115.5%, clipped down to 100% for the same reason as the
calibrated-state cap), derived from the between-state variance, not
tightened by density. That width is the honest answer, not a bug: 23
states still cannot support a tight one, and, per Section 13.2, the
interval has if anything gotten *more* justified for staying wide as more
states were added, not less, since the calibrated states keep turning up
more extreme values than the pooled model can express.

**On the 61.0% figure specifically**: this is *not* the exposure-weighted
aggregate ratio (`obdb_count.sum()/licensee_count.sum()` across the pooled
sample, which computes to ~71% with the 23-state sample). Those are two
different quantities: the aggregate is pulled up by a handful of large,
high-capture counties (Buncombe, Mecklenburg, Wake, Denver), while 61.0% is
what a WLS regression (weighted by licensee_count, so higher-exposure
counties still get proportionally more influence on the *fit* without
changing what the coefficients describe) predicts for a typical county at
average density. Since the pooled fallback is applied to arbitrary counties
nationally, most of which are small or medium, not large metros, the
per-county regression estimate is the correct anchor; using the
population-weighted aggregate would systematically under-correct exactly the
smaller, rural counties this correction exists to help. (An earlier version
of this module mixed the two (aggregate baseline, regression slope), which
was internally inconsistent by construction; both numbers now come from the
same weighted regression.)

### 5.3 A second, independent negative result: multi-source capture-recapture doesn't fix the bias

Section 5.4 below documents that naive 2-source (OBDB×OSM) capture-recapture
badly overestimates the true count due to correlated crowdsourcing bias. A
follow-up investigation tried whether a *proper* 3-source model, adding each
state's own licensee registry as a third, administrative list, and fitting a
log-linear model with all three pairwise source-dependence terms explicit
(the standard no-3-way-interaction closed-population capture-recapture
approach), could recover the correlation this project already knew was
there and produce a less biased estimate. Tested in CO and OR, the two
calibration states with record-level (not just aggregate) licensee data,
against known truth:

| | CO (truth ≈408-423) | OR (truth ≈285-297) |
|---|---|---|
| Naive 2-source (OBDB×OSM) Chapman | 1,040.5 | 531.8 |
| 3-source, independence assumed | 1,085.8 | 559.7 |
| 3-source, log-linear w/ pairwise interactions | **4,076.4** | **1,996.7** |

The 3-source model did not do better: it did dramatically worse, 4-8x off
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
appearing on the other (the same underlying trait: online visibility, being
well-established): this violates the independence assumption two-sample
capture-recapture needs and inflates the estimate. Loosening the spatial match
radius 300m→2000m recovered only ~9 more matches (92→101 of ~270 each),
confirming this is a real, structural coverage gap rather than a
record-linkage threshold artifact. **Conclusion: administrative registries, not
crowdsourced-pair capture-recapture, are the right calibration source here.**

## 6. Satellite-taproom sensitivity check

Oregon's OLCC data distinguishes primary licenses from "ADDITIONAL LOCATION"
licenses explicitly: a direct, real-world instance of the satellite-taproom
judgment call the project spec flagged. Excluding satellites (this project's
default) gives 285 breweries, 96% of the BA total; including them gives 347,
117% of the BA total. **BA's own count sits almost exactly at the
primary-license number**, which is why "one brewery per independent license,
satellites excluded" was chosen as the default rather than "one row per
physical taproom." The difference is concentrated in large metros: Multnomah
County (Portland) alone accounts for 22 of the 62 additional-location licenses
statewide.

## 7. Geographic-unit sensitivity

Per the project brief, county, CBSA, and place-level rankings were all built,
not just one. Places that are roughly coextensive with their core
county/CBSA (Boulder, Bend/Deschutes, Asheville/Buncombe, Fort
Collins/Larimer) rank highly at every level. Two mechanisms cause real
divergence between levels, both worth naming rather than smoothing over:

- **Denominator dilution at the county level**: Grand Rapids doesn't reach the
  county-level top 20 (Kent County's population is large enough to dilute the
  rate) but does at the CBSA and place level, where the denominator matches the
  brewery market more tightly.
- **Population-floor exclusion at the place level**: Traverse City, MI
  (Grand Traverse County) ranks #2 nationally by CBSA-level rate but has too
  small a place-level population (well under the 50k floor) to appear in the
  place-level ranking at all: exactly the "Bend/Traverse City" case the
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
  properly-formed, cookie-carrying POST request, not a Cloudflare bot
  challenge, so no evasion was attempted, and none was pursued. The
  county-level "Permit Counts" report endpoint worked and was used instead.
- **No Brewers Association bulk download or directory scrape.** Thirteen
  single state-total lookups were made (NC, MI, CO, OR, WA, TX, GA, WI, PA,
  IL, CA, NY, VA), each dated and cited inline in the relevant build script,
  per the project's explicit "no bulk download/scrape" constraint.
- **No Mississippi bulk alcohol-license data.** Checked thoroughly: the MS
  Dept. of Revenue's pages describe the Manufacturer/Brewpub permit
  categories but publish no roster; the only public lookup is
  `tap.dor.ms.gov`'s interactive, session-based per-record search: the same
  category of tool NC's individual-permit search fell into above, and not
  scripted around for the same reason. No Mississippi open-data portal
  exists (confirmed absent, unlike CO/TX/GA's Socrata portals). A clean "no,"
  not a gap in effort: Mississippi is not a calibration state.
- **No Ohio bulk liquor-permit data.** DataOhio's portal has no
  Commerce/liquor dataset; OPAL (the state's own licensing system) disabled
  bulk export from its Power BI report viewer, leaving only view/filter
  access; the actual permit-holder search
  (`comapps.ohio.gov/liqr/liqr_apps/PermitLookup`) is a stateful,
  single-criterion ASP.NET search form with no documented API. Not scripted
  around.
- **No Vermont bulk liquor-license data.** `data.vermont.gov`'s only
  Dept.-of-Liquor-Control datasets are traffic-stop demographic data, not
  licensing. The Department's own licensee database lives behind a
  Salesforce Experience Cloud login (`dllportal.my.vermont.gov`); the only
  public-facing tool is an interactive dashboard explicitly built for town
  clerks managing renewals, not a public bulk source.
- **No Minnesota bulk brewery-permit data.** The state licensing authority
  (Dept. of Public Safety, Alcohol and Gambling Enforcement) publishes no
  roster; its own interactive search tool is bot-gated (confirmed via a
  direct HTTP request returning a CAPTCHA challenge, not just documentation
  review), and obtaining bulk data requires a formal Minnesota Government
  Data Practices Act request, not a self-service download. No statewide
  Minnesota open-data portal exists.
- **No Tennessee state-level beer-license data at all, for a structural
  reason.** Tennessee's ABC does not regulate ordinary beer, only
  high-gravity beer (≥8% ABW), so ordinary brewery permitting is entirely
  local, city-by-city, with no statewide roll-up to request in the first
  place. A 3-source (OBDB/OSM/CBP) dataset was still built for face-validity
  purposes.
- **No Arizona or South Carolina bulk alcohol-license data.** Arizona's
  DLLC publishes no roster (only an interactive "ABC Online" search form
  and PDF reports); its FOIA page confirms a full roster requires a
  public-records request. South Carolina DOR's licensee lookup
  (`mydorway.dor.sc.gov`) is a cookie-gated interactive portal with no
  export, confirmed via a direct request returning a cookie-required error
  page rather than data. Both got 3-source (OBDB/OSM/CBP) datasets for
  face-validity purposes, same as Tennessee.

## 9. Model B covariate results (national, county-level, NB-GLM + state FE)

| Covariate | Coefficient | Interpretation |
|---|---|---|
| log(median household income) | +0.55 | Higher-income counties have more breweries, controlling for state |
| Median age | +0.046/year | Older-median-age counties have (slightly) more breweries: plausibly reflects established, higher-income communities rather than a youth effect |
| College enrollment share | +7.2 | Strong college-town effect, as expected |
| Tourism establishments per 10k | +0.029 | Tourism effect present and significant, as expected: this is a covariate being conditioned on, not a nuisance to explain away |
| 5-yr county population growth (%, 2019→2024 ACS vintage comparison) | +0.000895 (p=0.853) | **Clean null**: no detectable effect once the other covariates and state FE are already in the model. Chosen specifically because it varies *within* a state (a state-level-only covariate like an excise tax rate would be perfectly collinear with `C(state_abbr)` and inestimable). Top-20 residual ranking essentially unchanged after adding it (two adjacent-rank swaps only). 11 additional counties dropped from the model versus the pre-covariate baseline, all due to a genuine cross-vintage geography mismatch (Connecticut's 2019→2024 switch from counties to Planning Regions; two Alaska census areas split out of the former Valdez-Cordova Census Area), not missing-data suppression: correctly left as `NaN` and dropped rather than mismatched. |

Top of the shrunken residual ranking ("more breweries than covariates predict"):
Buncombe NC (Asheville), Charleston SC, Fulton GA (Atlanta), St. Louis city MO,
Travis TX (Austin), Richmond city VA. Full table:
`data/processed/us_county_residual_rankings.parquet`.

The raw (unshrunk) residual ratio is dominated by small-expected-count noise
(e.g. a county "expected" 0.27 breweries that has 2 looks like a 7x outlier): the same instability the project brief warns about for raw rates. The same
Poisson-Gamma shrinkage used for Model A was applied to the residual, centered
on each county's own covariate-based expectation rather than the flat national
mean.

### 9.1 Leave-one-state-out validation: the top list is less stable than it looks

`scripts/validate_model_b_loso.py` refits the identical model 51 times, each
time holding out one state's counties from training and predicting them from
a model that never saw that state's own data (the held-out state's missing
fixed-effect term is imputed as the mean of the other fitted state effects: the one non-obvious methodological choice here, documented in the script).

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
| Buncombe NC | 1 | 4 | Holds up: residual actually *grows* without NC in training |
| St. Louis city MO | 4 | 10 | Holds up reasonably |
| Richmond city VA | 6 | 14 | Holds up reasonably |
| Charleston SC | 2 | 33 | Drops substantially |
| Travis TX | 5 | 104 | Drops substantially |
| Fulton GA | 3 | 242 | **Collapses**: no longer looks like an outlier at all |

Fulton County's case is the clearest illustration of *why* this matters: a
large share of "more breweries than expected in Fulton" turns out to be
Georgia's fitted state effect, not a Fulton-specific signal: when Georgia's
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
  precise: they're shrunk toward priors for exactly this reason, but shrinkage
  reduces noise, it doesn't manufacture missing ground truth.
- Any state without its own calibration data is carrying OBDB's raw undercount
  (7-54% observed range across the 23 calibration states/DC, before the TX/WI
  reference-quality caveats in Section 5.1) partially corrected by a wide,
  honestly-uncertain interval: not a precise correction.
- The choropleth and rankings are **not** capture-rate-corrected by default
  (the map explicitly says so); `capture_rate_model.apply_correction()` exists
  to produce a corrected version but doing so at every county nationally
  compounds the state-vs-density confound described in Section 5.2.
- OSM data has been fetched for all 50 states + DC (`data/raw/osm/`), but it
  is not incorporated into the headline county/CBSA/place datasets or either
  model: the only place it's used quantitatively is the NC capture-recapture
  diagnostic in Section 5.4, and per that section's finding, using it as a
  second signal at national scale would need the same correlated-crowdsourcing
  caveat, not a straightforward "more data is better" treatment.

## 11. Case study: manual verification of a single geography (Santa Cruz, CA)

Prompted by a user's firsthand knowledge of the Santa Cruz, CA brewery scene
conflicting with its mid-tier ranking, a full manual investigation was run: partly to answer the specific question, partly as a worked example of what
this project's coverage-error argument looks like at the level of one real
place rather than an aggregate percentage.

**What was found:**
- **Balefire Brewing Company** (opened October 2023, confirmed still
  operating via current reviews) is missing from **both** OBDB and OSM: not
  a pipeline bug, direct evidence of the exact mechanism (crowdsourced
  sources lag new openings) this project's whole coverage-error argument
  rests on.
- Two other names that looked like possible gaps were checked and confirmed
  **not** to be: Santa Cruz Ale Works and Boulder Creek Brewery are both
  marked "closed" in OBDB, and both are in fact closed per current listings: the classification is correct.
- "Laughing Monk" and "Other Brother," which surfaced in a broad web search
  near the area, are based in San Francisco/Sunnyvale and Seaside/Monterey
  County respectively: not Santa Cruz County breweries at all.
- **The dominant effect is geographic-unit dilution, not a missing-data
  gap**: Santa Cruz *city* (44,425 adults 21+, just under the 50k floor) has
  a raw rate of 11.3/100k: Boulder/Asheville-tier. Santa Cruz *County*
  (197,974 adults 21+, including Watsonville and inland areas well outside
  the brewery-dense coastal core) dilutes that to 5.6/100k. This is the same
  mechanism already documented for Grand Rapids/Traverse City in Section 7,
  now confirmed by an independent, user-prompted case.
- Adding the one confirmed missing brewery moves the county rate from 5.56
  to 6.06 per 100k, real, but modest; it does not by itself explain a
  "low" ranking. The unit-dilution effect is the larger factor.
- No geocoding, inclusion-filter, or pipeline bug was found in this
  investigation: county assignment and population figures check out.

**Does this generalize? A systematic follow-up, and its limits.** Per-city
manual investigation obviously doesn't scale to ~3,200 counties. The natural
systematic alternative is *unioning* OBDB with OSM by name+location match
(not capture-recapture: a union only combines what was actually observed by
at least one source, so it isn't subject to the correlated-crowdsourcing
bias that sank the estimation approaches in Sections 5.3-5.4), implemented in `scripts/build_obdb_osm_union.py`. Building it surfaced two
more real bugs (documented in the script and in the git history): OBDB
records missing lat/lon were unmatchable by construction (fixed by
geocoding before matching), and `capture_recapture.normalize_name()`
stripped "Co" but not "Company" as a brewery-name suffix, causing systematic
false negatives (fixed). Even after both fixes and adding OSM-internal
deduplication, a manual sample of the tool's "genuinely absent from OBDB"
output still contained real false positives: mostly OBDB's compound
dual-brand names (e.g. "Automatic Brewing Co. / Blind Lady Alehouse") not
fuzzy-matching a single-brand OSM name, and a greedy-matching artifact where
one OSM record can claim another's correct match slot. **The tool is not
integrated into any correction**: it reduces the review burden from
"investigate every US city" to "review a few thousand flagged candidates,"
it does not eliminate manual verification, and its current headline count
should be read as an upper bound requiring further matching refinement (see
README "Known limitations"), not a validated addition to any total.

**Update: round 3 (fresh false-positive measurement) and round 4 (targeted
fix).** The compound-name and greedy-matching bugs above were both fixed
(compound names now match via `name_variants()` splitting on `/`; matching
now solves a global optimal assignment via
`scipy.optimize.linear_sum_assignment` instead of greedy nearest-then-best,
so one record can no longer steal another's correct match slot). A fresh
manual spot-check (n=28, random sample of the post-fix "genuinely absent"
pool) measured a ~14% (4/28) residual false-positive rate from three
distinct, previously-unidentified causes:

1. **Coordinate imprecision.** OBDB's address-geocoded point can sit several
   kilometers from OSM's actual mapped node for the same real brewery.
   Confirmed case: "Cellarmaker Brewing Company" (San Francisco): OBDB's
   point is 3,579.77m from the nearer of two OSM "Cellarmaker" nodes (the
   second, at 12,266.33m, is a genuinely different reference and correctly
   stays unmatched). Both exceed the 300m default `max_distance_m`.
2. **Near-threshold rename/abbreviation pairs.** E.g. "Wild Heaven Beer"
   (GA) vs. OBDB's "Wild Heaven Craft Beers": 62.9m apart, name score 64.7,
   just under the default 65 `name_threshold`.
3. **No-address OBDB records.** Some `planning`-status listings have no
   street address at all, so `fill_missing_coords()` can never geocode them: these can never match regardless of true proximity. Structurally
   different from causes 1-2: no matching-threshold adjustment can fix a
   record with no coordinates to compare.

Round 4 addressed causes 1-2 with an opt-in second-pass fallback in
`match_records()` (`fallback_stages` parameter, default `None` so all
existing callers/tests are unaffected). Records still unmatched after the
primary (300m, score≥65) pass get two additional Hungarian-optimal
assignment attempts, each trading exactly one constraint for slack while
holding the other tight: deliberately conservative, not just "loosen
everything":

- **(150m, score≥55)** for cause 2 (near-threshold renames): distance stays
  far tighter than the primary radius, which is what justifies accepting a
  lower name score.
- **(5,000m, score≥90)** for cause 1 (coordinate mismatches): radius opens
  to state/city scale, justified by requiring a near-exact name match.

`scripts/build_obdb_osm_union.py` now wires
`FALLBACK_STAGES = [(150, 55), (5000, 90)]` into both `match_records()`
calls. Validated: Cellarmaker now matches (via the 5,000m/90 stage, at
3,579.77m, name score 100); Wild Heaven Beer now matches (via the 150m/55
stage); none of the three previously-confirmed-correct cases (Blind Lady
Alehouse, Maine Beer Co., Great Lakes Brewing Co.) regressed. The
conservatism of the wide-radius stage was independently confirmed, not just
assumed: two same-name pairs correctly stayed *unmatched* because they are
genuinely different locations ~19-22km apart ("Crafty Bastard Brewery West"
vs. OBDB's downtown Knoxville listing; "Civil Society Brewing Co" West Palm
Beach vs. OBDB's Jupiter, FL listing).

National "genuinely absent" count: 3,899 → 3,374 (round 1-2 fixes) → 3,261
(round 2 matching-algorithm fix, measured but not yet spot-checked) →
**2,829 (round 4, 40.8% more breweries than OBDB alone, down from 47.0%)**.
A fresh n=25 spot-check (seed 20260830) of the round-4 output found just 1
residual false positive from the two targeted causes (a brewery whose
OBDB/OSM coordinates are 9.5km apart, beyond even the widened 5km cap,
confirmed via web search to be a single real location, i.e. a genuine
geocoding discrepancy the conservative cap was not designed to catch) plus
3 records from the still-unaddressed cause 3 (no-address `planning`
listings). Net: the targeted false-positive rate fell from ~14% to ~4% in
this fresh sample; the tool's output remains an upper bound requiring
manual review, not a validated correction.

## 12. Spatial autocorrelation: are high-density counties clustered or independent?

Every model in this project (Model A's shrinkage prior, Model B's NB-GLM)
treats counties as statistically independent observations. That's a
convenience assumption, not a claim about the world, and it was checked
directly (`scripts/build_spatial_hotspots.py`,
`data/processed/us_county_spatial_hotspots.csv`), using CONUS counties only
(3,109 of them, Alaska, Hawaii, and territories excluded since they aren't
land-contiguous with the mainland and Queen contiguity requires shared
borders).

**Global Moran's I** on the raw shrunken rate (`eb_posterior_rate_per_100k`),
Queen contiguity, 9,999-permutation inference: **I = 0.360** (expected under
complete spatial randomness ≈ 0), analytic z = 34.1, p ≈ 2.7×10⁻²⁵⁴
(analytic) / p = 0.0001 (permutation floor). This rejects spatial
randomness unambiguously: county-level brewery density is positively and
strongly spatially autocorrelated, i.e. a "beer belt" pattern is real, not
an artifact of aggregate summary statistics.

**Local Getis-Ord Gi\*** (binary Queen weights, `star=True`, two-sided
analytic z-test computed directly rather than via `esda`'s built-in
`p_norm`/`p_sim`, whose one-/two-sided convention was ambiguous across
different `alternative=` settings in testing) flags per-county hot/cold
spots. With ~3,100 simultaneous tests, Bonferroni was judged too
conservative (it also assumes independence between tests, which the
confirmed spatial autocorrelation directly violates); Benjamini-Hochberg
FDR at q<0.05 was used instead, and both raw-p and FDR-q counts are printed
by the script so the difference is visible (420 counties clear uncorrected
p<0.05, vs. ~155 expected by chance; FDR trims this to 222 significant
counties, still a large excess over the null).

**Result: 220 hot spots, 7 cold spots.** The hot spots are not scattered: their connected-component structure (via the same Queen contiguity graph)
collapses into 13 components, 8 multi-county, with five real regional
clusters:

| Region | Counties | States |
|---|---|---|
| Colorado Front Range / Rockies | 64 | CO/MT/WY/ID |
| Pacific Northwest | 52 | OR/WA/CA |
| New England | 50 | ME/VT/NH/MA/NY |
| Northern Michigan | 16 | MI |
| Southwest Michigan (Kalamazoo/Grand Rapids belt) | 11 | MI/IL |

Top hot spots by z-score are dominated by exactly these regions: Grand,
Boulder, Larimer, Jefferson, Gilpin, Eagle, Summit, and Clear Creek
Counties (CO); Yates, Schuyler, and Seneca Counties (NY, Finger Lakes);
Skamania, Hood River, Washington, Lane, and Clackamas Counties (OR/WA);
Chittenden (VT); Knox (ME); Leelanau (MI). The 7 cold spots are dense urban
cores with low per-capita counts: Bergen (NJ), New York/Manhattan (NY),
Hudson (NJ), Fulton (GA), Bronx (NY), the expected mirror image of a
per-capita-rate hot spot analysis in dense metros.

**Robustness to the capture-rate correction**: re-running on
`eb_posterior_rate_per_100k_corrected` gives Moran's I = 0.299 (still highly
significant) and 186 FDR-significant hot spots (0 cold spots), with 96.2%
label agreement against the raw-rate run and 145 of 217 raw hot spots
confirmed hot under the correction. The clustering finding is not an
artifact of OBDB's uneven state-level capture rate.

**Implication for the two existing models**: this is grounds for a
follow-up, not a retraction: a spatially-aware model (e.g. a conditional
autoregressive prior instead of the flat national-mean or purely
covariate-based priors currently used) would likely produce tighter,
better-calibrated estimates for counties inside one of these five clusters
than the current models' independence assumption allows, since a county's
neighbors carry real information about it that neither Model A nor Model B
currently uses.

### 12.1 A spatially-aware alternative to Model A (`fit_spatial_car_model.py`)

The follow-up flagged above was built and validated. **Model**: a Bayesian
Negative-Binomial ICAR (intrinsic conditional autoregressive) model: county
counts ~ NegBinomial(expected_count · exp(φ)), where φ, the spatially
structured random effect, is smoothed toward the average of each county's
Queen-contiguity neighbors' effects (the same graph construction as Section
12) rather than shrunk toward the flat national mean the way Model A's
Poisson-Gamma prior is. Fit via PyMC/NUTS, CONUS counties only (3,109; 113
non-CONUS counties/territories keep Model A's rate unchanged, since there's
no valid contiguity graph for them).

**Fitting practicalities**: 4 chains, 2,000 tuning + 2,000 draw iterations
for the production fit (~260s), converges cleanly (0 divergences, rhat max
1.0068 across beta0/sigma_phi/alpha, phi rhat max 1.0055, phi ESS bulk min
1,216). An initial faster holdout-validation fit (800 draws) showed rhat up
to 1.039: still below a reasonable 1.05 tolerance but not the stricter
1.01 bar the production fit meets, so the holdout fit's draws were
increased to match the production fit's settings (2,000/2,000); the result
was materially unchanged (+0.1259 log-lik/county vs. the original +0.1267),
confirming the original finding wasn't a sampling artifact.

**Validation, not just a different point estimate**: a seeded 80/20
train/test split compares held-out mean log-likelihood between Model A
(fit on the train fold, flat national mean) and the spatial model (train-fold
likelihood, test-fold φ read directly from the contiguity graph, since an
ICAR effect for a held-out county is fully determined by its neighbors'
fitted values regardless of whether that county's own count was in the
training likelihood): Model A −1.2532/county vs. spatial model
−1.1264/county, a genuine out-of-sample improvement, not merely a better
in-sample fit.

**Qualitative validation against Section 12's independently-derived
clusters**: this is the check that most directly answers "is this real
signal or just noise with a spatial label on it": among the 88 counties
that are both population-floored (≥50k adults 21+) and inside a confirmed
Section-12 cluster, hot-spot counties move UP in the spatial ranking
relative to Model A by a mean of +52.6 ranks (median +14.5; 75% moved up),
cold-spot counties move DOWN by a mean of −68.4 ranks, and the remaining
not-significant counties drift only slightly (mean −6.1): exactly the
pattern a real spatial effect should produce, derived from a completely
separate analysis (Getis-Ord Gi* clustering) than the one that fit this
model. Overall rank correlation with Model A stays high (Spearman ρ=0.86,
n=799 population-floored CONUS counties): this is a targeted correction
concentrated where the spatial signal says it should apply, not a
wholesale reshuffle. Individual examples: Wayne County, NY (a hot-spot
county) moves from rank 712 to 140 (+572); Troup County, GA (not
significant in the Gi* test but adjacent to genuinely low-density
neighbors) drops from 239 to 665 (−426).

Outputs: `data/processed/us_county_car_shrunken_rankings.parquet` (3,222
counties), `data/processed/us_county_raw_vs_car_rankings.csv` (799
population-floored CONUS counties, with `spot_type` joined in from the
Section 12 hot-spot analysis for exactly this comparison). **Not adopted as
the project's default ranking**: Model A remains the headline shrinkage
estimate, but this is now the strongest evidence in the project that doing
so would be a real accuracy improvement, not a stylistic preference: it
needs no new data, only a different (and validated) prior.

## 13. Symmetric and complementary views: brewery deserts and the state-level rollup

### 13.1 Brewery deserts (`scripts/build_brewery_deserts.py`)

Every ranking artifact in this project surfaces counties with unexpectedly
*high* density. The inverse (large-population counties with unexpectedly
*low* density, i.e. candidate areas of unmet market potential) is a
trivial re-sort of data already on hand
(`data/processed/us_county_brewery_deserts.csv`, 817 counties, population
≥50,000 floor applied for the same small-county-noise reason as every other
ranking here) but had never been produced as its own artifact.

Two views are computed: (1) the pure bottom of the corrected-shrunken
ranking among population-floored counties, and (2) a population-weighted
cross-cut of the 100 largest counties nationally by `adults_21plus`,
re-sorted by lowest corrected density: since a bottom-ranked 50k-population
county is a much smaller absolute "opportunity" than a bottom-ranked
1M+-population county the pure rank-based view would treat identically.

Top of the pure bottom-of-ranking list: Passaic County NJ (376,703 adults,
0 breweries), Pinal County AZ (352,123 adults, 0), Jefferson Parish LA
(321,479 adults, 0), Osceola County FL (309,697 adults, 0), Gwinnett/
Cherokee/Clayton Counties GA (Atlanta suburbs), Fort Bend County TX
(Houston suburb), Hudson County NJ (NYC suburb). The population-weighted
cross-cut additionally surfaces large metros with striking absolute gaps
that don't make the pure bottom-20: Miami-Dade FL (2.1M adults, 13
breweries), Harris County TX/Houston (3.4M adults, 36), Queens NY (1.8M
adults, 11).

**Pattern**: 35 of the bottom-50 desert counties are in the South (largely
GA/TX/LA/FL suburban and exurban counties); the Midwest is essentially
absent from the bottom-50. Qualitatively, most bottom-ranked deserts are
large-population suburbs or exurbs immediately adjacent to a metro with a
thriving brewery scene (Gwinnett/Cherokee/Clayton outside Atlanta,
Fort Bend outside Houston, Hudson/Bergen/Passaic outside NYC, Osceola
outside Orlando) rather than remote or purely rural counties: breweries
appear to cluster into urban cores and gentrifying neighborhoods and skip
nearby large-population suburbs even where the underlying metro clearly
supports the category. Demographic differences between desert and
non-desert counties are real but small (median age 37.7 vs. 39.7; tourism
establishments 1.63 vs. 2.33 per 10k; median household income ~$78.1k vs.
~$80.0k): no single covariate dominates.

Raw-shrunken and corrected-shrunken desert lists agree closely (Spearman
ρ=0.976, 17 of the bottom 20 shared): unlike the high-density ranking,
where the capture-rate correction substantially reshuffles who's on top
(Section on Texas clipping, README Key Findings), it barely reshuffles who's
at the bottom. This makes sense mechanically: the correction scales a
county's estimate by `1/capture_rate`, which has the largest absolute effect
on counties that already have a meaningful brewery count to scale: a
county with 0-1 breweries stays near 0-1 regardless of the multiplier
applied.

### 13.2 State-level rollup (`scripts/build_state_rollup_table.py`)

No state-level summary artifact existed despite state being a first-class
unit of this project's own methodology (the capture-rate model is
calibrated per state). `data/processed/state_rollup_table.csv` (51 rows: 50
states + DC) aggregates: calibration status and capture rate, total
OBDB/corrected brewery counts and their gap, population-weighted
(`adults_21plus`-weighted, not a naive county average) corrected rate per
100k, and mean/median `rank_change` among that state's population-floored
counties.

**Top 10 states by population-weighted corrected rate**: AK (17.26), MT
(16.74), VT (15.33), ME (15.19), NH (11.31), SD (10.34), WY (9.35,
calibrated), CO (9.31, calibrated), OR (8.82, calibrated), NM (7.46): dominated by small-population pooled-extrapolation states, a direct
consequence of the per-100k-adults denominator; WY entering the calibrated
top 3 this round (up from being pooled-estimated before) is itself a
consequence of its capture rate clipping to 1.0 (see Section 5.1).

**Biggest absolute raw-vs-corrected brewery-count gaps** (corrected −
OBDB): CA (+509, 763→1,272), PA (+304), VA (+236), NY (+166), NC (+162),
OH (+124), WI (+115), MN (+85), GA (+81), FL (+80): dominated by large
states with low-to-mid capture rates, since the gap scales with both county
count and `1/capture_rate − 1`.

**A structural finding worth flagging methodologically**: the 23
directly-calibrated states show far more extreme and more variable
`rank_change` (state-mean SD = 60.4, range [−109, +80]) than the 28
pooled-estimate states (SD = 14.2, range [−15, +48]). This tracks
mechanically with capture-rate spread: calibrated states range from 0.465
(VA) to a clipped 1.0 (TX, IL, WV, WY, MO), so low-capture-rate calibrated
states get pushed sharply up in rank (PA +80.0 mean, VA +75.4, CT +45.8)
while clipped-to-1.0 states get pushed sharply down (IL −109.2, WV −108.2,
MO −107.8). The pooled regression, by construction, can only produce
capture rates in a narrow band (~0.50–0.80 around a 0.610 baseline,
modestly density-adjusted): it structurally cannot express the extremes
real calibration data produces. Notably, this gap *widened* as more states
were calibrated this round rather than narrowing (the calibrated/pooled SD
ratio went from ~3.2x at 13 states to ~4.3x at 23): more real measurements
kept revealing more extremes the pooled model can't reach, not fewer.
**This means the correction's effect on the 28 uncalibrated states is
systematically muted relative to what direct state-specific measurement
would likely show**: not because those states truly need smaller
corrections, but because the pooled model has no mechanism to express
state-specific extremes. This reinforces, from a new angle, the same
conclusion Section 5.2 already reached: the pooled fallback is a reasonable
default given the data available, but its outputs for uncalibrated states
should be read as conservative/muted, not as equally
precise to the 23 calibrated states' outputs.

## 14. Visualization: side-by-side comparison and collision-aware labeling

**Three-panel comparison figure** (`scripts/build_map_comparison.py`,
`data/processed/us_brewery_density_comparison.png`) renders the raw,
empirical-Bayes-shrunken, and capture-rate-corrected-shrunken maps on one
shared color scale (CONUS only, Alaska/Hawaii insets dropped for
compactness) so the correction's uneven effect (Section 5, README Key
Findings) is visible without cross-referencing separate PNGs. The shared
scale's bins are deliberately computed from the *population-floored* range
of the corrected panel (the widest of the three), not the raw panel's
unfiltered range: the raw panel has extreme outliers (national max
~317/100k) that come entirely from sub-50k-adult counties which are grayed
out in all three panels regardless, so basing the scale on unfiltered data
would have compressed the two smaller-range panels into visual uniformity
for no informational gain. Verified both numerically and visually: Texas
counties move by <0.01/100k between the shrunken and corrected panels
(capture rate clipped at 1.0, so no room for upward correction), while
Pennsylvania and Georgia counties visibly jump one to two color bins darker
(Allegheny County PA: 3.42→7.04/100k; Fulton County GA: 3.43→7.23/100k;
state means: PA 3.00→5.98, GA 1.46→2.39, vs. TX 1.48→1.51).

**Collision-aware label placement** (`src/breweries/map_labels.py`,
used by `build_choropleth.py` and `build_corrected_rankings.py`). Prior
choropleths labeled only a fixed, hand-picked list of 8 face-validity
anchor cities, leaving many genuinely high-density counties unlabeled. The
new module places labels via real text-bounding-box collision detection: `Text.get_window_extent(renderer)` after `fig.canvas.draw()`, trying up to
12 candidate offset positions per label (near/readable positions first,
farther/less-preferred ones as fallback) and skipping a candidate silently,
rather than overlapping, if none avoid a collision with an already-placed
label, the legend's own bounding box (reserved explicitly before label
placement), or a marker dot's small reserved footprint. The original 8
anchor cities are placed first at maximum priority and always win contested
space; up to 22 additional labels are then generated from each map's actual
top-rate, population-floored counties and placed in priority order, using
TIGER's `NAMELSAD` (not the bare `NAME`) specifically because Virginia's
independent cities share a bare county name with a same-named county (see
Section on the Virginia join-key bug): labeling off `NAME` would risk
mislabeling a high-rate independent city as the wrong, much lower-rate
county. Auto-generated candidates within ~80km of an already-placed anchor
are excluded up front, so e.g. an auto-label doesn't compete with "Boulder,
CO" for the same visual space its own county's anchor already occupies.
Verified by direct visual inspection on all four affected maps (raw,
raw-floored, corrected, corrected-floored): no overlapping text, no
obstruction of the legend or title, and previously-unlabeled genuinely dark
counties (e.g. Skagit County WA, Coconino County AZ, Natrona County WY,
Loudoun County VA) now correctly labeled.

**Interactive map: table view and a mobile layout fix**
(`scripts/assemble_interactive_map_html.py`). A searchable, sortable Table
tab was added alongside the existing map view: the same county/CBSA data,
filterable by name/state and sortable by any column via a click, with a row
click jumping back to the map with that unit selected: for finding a
specific place by name rather than only by visual exploration. Separately,
a user-reported mobile screenshot surfaced two real, independent bugs (see
README "Codebase audit" for the full writeup): a missing
`<meta name="viewport">` tag, which made every `max-width` mobile media
query in the stylesheet silently inert on a real phone regardless of screen
size; and, once that was fixed, a classic nested-flexbox overflow gap (a
horizontally-scrollable control strip lacked an explicit width constraint
from its container, so instead of scrolling internally it was pushing the
entire page ~250px wider than the viewport). The second bug was diagnosed
by injecting a temporary debug probe measuring `document.body.scrollWidth`
and `getBoundingClientRect()` directly, after a purely visual screenshot
comparison proved unreliable: local headless-Chrome testing in this
environment turned out to have its own unrelated ~500px viewport-size floor
that ignored the requested `--window-size`, which could easily have masked
or been mistaken for the real bug if diagnosis had stopped at "does the
screenshot look right."

## 15. The adopted model: covariates + state FE + BYM2 spatial random effect

Section 12.1 validated a pure spatial (ICAR) alternative to Model A and
flagged the natural next step: Model B's covariates and the CAR model's
spatial term had never been combined, even though each captured real signal
the other didn't. This section documents that combination
(`scripts/fit_combined_spatial_covariate_model.py`), its validation, and its
adoption as the project's headline county-level ranking: replacing Model A
in that role. Model A remains available as a toggleable comparison
everywhere it was previously shown, and remains the model behind CBSA/place
rankings, which have no shared-neighbor-graph equivalent (a CBSA or place
doesn't border its neighbors the way a county borders its neighbors, so a
BYM2 spatial term isn't defined at those levels).

### 15.1 Model specification

Negative-binomial GLM: `count_i ~ NegBinomial(mu_i, alpha)`,
`mu_i = exposure_i * exp(X_i * beta + spatial_i)`, where `X_i` is an
8-covariate design (log median household income, median age, college
enrollment share, tourism establishments per 10k, **log population density per
sq mi**, 5-year population growth, unemployment rate, median gross rent) plus
49 state dummies, and `spatial_i` is a **BYM2** (Besag-York-Mollié, Riebler et
al. 2016 parameterization) random effect combining a structured and an
unstructured component:

`log_density` was added after publication and is not part of Model B's
original 7-covariate design (Section 9). Its absence was the model's largest
defect: with no urbanicity term, the BYM2 spatial effect was the only
component able to express urban/rural variation, and smoothing it was that
term's entire job. See Section 18.3 for the measured bias this produced and
how it was found.

Optionally, `exposure_i` also carries the state capture rate
(`CAPTURE_RATE_OFFSET`), which de-confounds the state fixed effect from OBDB's
state-level coverage gap. Off by default — it changes the reported quantity
from an observed rate to a true rate. See Section 18.3.

```
spatial_i = sigma * (sqrt(rho/scale) * phi_i + sqrt(1-rho) * theta_i)
phi   ~ ICAR(W)            -- structured: smoothed toward Queen-contiguity neighbors
theta ~ Normal(0, 1), iid  -- unstructured: county-specific, no cross-county structure
rho   ~ Beta(1, 1)         -- mixing weight, structured vs. unstructured
sigma ~ HalfNormal(2)      -- overall spatial standard deviation
```

`scale`, the geometric mean marginal variance of the ICAR structure, is
computed **exactly** (not approximated): letting `Q = diag(neighbor_count) - W`
(rank-deficient by one for a connected graph) and `v = ones(N)/sqrt(N)`
(its null vector), `Q + vv^T` is invertible and `(Q+vv^T)^-1 - vv^T` gives
the Moore-Penrose pseudoinverse restricted to `v`'s orthogonal complement: mathematically identical to what R-INLA's `inla.qinv(..., constr=...)`
computes, just via a dense N×N solve (N=3,109 CONUS counties makes this
trivial, <1s) rather than a sparse one. `scale = 0.5686`.

### 15.2 Fitting

Full NUTS throughout (no ADVI/MAP compromise needed) via PyMC. The final
production fit uses a longer run than the initial attempt, after an
explicit request to push convergence further: **4,000 tuning + 4,000 draw
iterations, 6 chains, target_accept=0.97** (up from an initial 2,000/2,000/4/
0.95). A macOS-specific SIGSEGV (Accelerate's threaded BLAS conflicting
with PyMC's multiprocessing worker pool) was found and fixed by pinning
every worker to a single BLAS thread (`OMP_NUM_THREADS`,
`VECLIB_MAXIMUM_THREADS`, `OPENBLAS_NUM_THREADS`, `NUMBA_NUM_THREADS` all
set to `1` before numpy/pymc import) and letting multiprocessing provide
the parallelism across chains instead.

**Convergence** (final production fit, after the longer run): 0
divergences. `theta_iid` and the scalar parameters (`alpha`, `sigma_bym`)
converge cleanly (rhat ≤1.002, ESS in the thousands). `phi_icar` converges
acceptably (rhat 1.043-1.049, ESS 42-115). The 56 covariate/state-FE
coefficients (`beta`) remain the softest part of the fit (rhat ~1.06-1.07,
ESS 40-90): improved from ~1.11/28 in the initial shorter run, but not
fully under the strict 1.01 bar even after 2x draws/tune, 1.5x chains, and
a higher target_accept. This is reported honestly rather than re-run
indefinitely chasing a marginal gain: the held-out log-likelihood
comparison below (which only needs predictive accuracy, not clean
per-coefficient posteriors) is robust to this; individual `beta` point
estimates should be read with the caveat that a handful of state-dummy
coefficients are mixing slowly, plausibly reflecting partial
non-identifiability between a compact state's fixed effect and its own
ICAR neighborhood signal (both explain some of the same within-state,
between-county variance).

### 15.3 Held-out validation: all four models on the identical split

Same seeded 80/20 train/test split (seed=42) used throughout this memo's
spatial work, run across Model A, Model B (no spatial term), the CAR-only
model, and the combined model, on the identical 3,109-county CONUS
universe:

| Model | Held-out log-lik/county |
|---|---|
| **Combined (covariates + state FE + BYM2)** | **−1.077** |
| CAR-only (pure ICAR, no covariates) | −1.126 |
| Model A (flat national mean) | −1.253 |
| Model B (covariates + state FE, no spatial) | −1.354 |

Model A and CAR-only reproduce Section 12.1's figures almost exactly
(−1.253/−1.126 vs. −1.2532/−1.1265), confirming the split and replication
are faithful. **The combined model wins outright**: a real, nontrivial
improvement over CAR-only alone (+0.049 log-lik/county), not a wash. The
more consequential finding is what Model B alone does: it generalizes
*worse* than the flat mean, meaning its covariates are actively overfitting
without the spatial term to regularize them. (Model B's own statsmodels
NB-GLM MLE needed a warm start from a Poisson-GLM fit to avoid a degenerate
near-zero-alpha collapse, the exact pathology `shrinkage.py` already warns
about, a real numerical fragility in the covariate-only specification,
separate from but consistent with its poor held-out performance.) Combining
covariates with spatial structure is not one component drowning out the
other: the spatial term alone (CAR-only) leaves real covariate-explainable
variance on the table, and the covariates alone (Model B) don't generalize
without spatial regularization. Posterior `rho` (structured share of
spatial variance): mean 0.970, 89% ETI [0.915, 0.998]: the ICAR component
dominates almost entirely over the unstructured `theta`.

**Qualitative check against Section 12's independently-derived clusters**
(population-floored CONUS, n=799, Spearman ρ=0.839 vs. Model A, a targeted
correction, not a reshuffle): confirmed hot-spot counties move UP by a mean
of 45.3 ranks (median +14, 63% moved up); confirmed cold-spot counties move
DOWN by a mean of 50.4 ranks; not-significant counties drift slightly down
(mean −5.3): the same qualitative pattern CAR-only showed, now with
covariates also contributing.

### 15.4 Two bugs found and fixed while adopting this model

**Posterior mean vs. median.** The first pass reported the posterior
*mean* of the rate samples as the point estimate. For a low-exposure county
with wide posterior uncertainty in its linear predictor, `exp()` of a
wide-variance quantity has mean substantially greater than median (the
standard log-normal mean-vs-median gap, mean ≈ median × exp(σ²/2)): found
via a real case, Mineral County CO (0 observed breweries, 640 adults 21+),
which reported 983 breweries/100k under the mean-based estimator. Switched
to the posterior **median** (50th percentile of the same samples already
being computed for the CI): standard practice in Bayesian small-area
disease mapping for exactly this reason. This alone only partially fixed
the Mineral County case (983 → 879), which led to root-causing a second,
more consequential bug:

**`tourism_estab_per_10k` winsorization.** This Model B covariate (tourism
establishments per 10,000 residents, `build_national_county_dataset.py`) is
a small numerator over a small population denominator for the smallest
counties, with no protection against the resulting blowup: Mineral County,
CO: 12 establishments / 640 adults = 164.6, vs. a national mean of 3.9 and
99th percentile of 34.8; 42 counties (mostly remote Alaska boroughs and
tiny mountain/lake counties) sit 2-50x past the 99th percentile. Feeding a
value 15-40 standard deviations outside a linear model's effective training
range is guaranteed to produce out-of-distribution nonsense by construction: this, not the mean/median choice, was the dominant driver of the 983
figure (median dropped only to 879 with the same uncapped covariate, then
to 38.8 once the covariate itself was capped). This bug **predates this
round of work and was already silently present in Model B**, undetected
only because every reported Model B table is population-floored at 50k
adults, which happens to exclude every one of the affected tiny counties.
Fixed by winsorizing `tourism_estab_per_10k` at its 99th percentile (33
counties capped on the final data vintage) in `build_national_county_dataset.py`: preserves the real tourism signal for the ~99% of counties where the
ratio is well-behaved while preventing a few small-denominator artifacts
from dominating a linear model's fitted values for any county, calibrated
or not. Post-fix, the maximum combined-model rate nationally is 76.7/100k
(Ouray County, CO, a real, population-floored-excluded but plausible
value for a small mountain county genuinely inside the confirmed Colorado
Front Range/Rockies hot-spot cluster from Section 12), not 983.

Both fixes are in the shared covariate/model-output pipeline, so both
transparently improve Model B's own (unpublished-as-a-ranking) fitted
values too, even though Model B itself was never re-adopted as a standalone
ranking.

### 15.5 Downstream adoption

`scripts/build_choropleth.py`, `scripts/build_top50_table.py`, and
`scripts/build_interactive_map.py` were repointed from
`us_county_shrunken_rankings.parquet`/`eb_posterior_rate_per_100k` to
`us_county_combined_model_rankings.parquet`/`combined_posterior_rate_per_100k`.
The interactive map keeps the plain shrunken/raw/floored rates as
toggleable alternatives (default mode renamed "Model rate") rather than
removing them, consistent with this project's pattern of superseding a
headline number without deleting the comparison. CBSA and place-level
outputs are unchanged (Model A, per Section 15's opening paragraph).

## 16. Two more attempts this round: one covariate success, one rejected spatial idea

**Model B covariates, round 3** (`src/breweries/sources/covariates.py`,
`scripts/build_national_county_dataset.py`, `scripts/fit_national_models.py`):
county unemployment rate (ACS B23025) added with a significant negative
effect (coefficient −6.347, SE 1.811, p=4.6e-04: a 1-SD increase, ~2.65
points on a 4.8% mean, associates with ~15% fewer breweries than expected).
Median gross rent (ACS B25064, an explicit proxy for commercial
cost-of-doing-business, which isn't measured at county granularity) added
with a clean near-null (coefficient +0.000195, p=0.170). County wet/dry
alcohol-sales status was investigated and **not added**: NIAAA's Alcohol
Policy Information System (`alcoholpolicy.niaaa.nih.gov`) was checked
directly and its jurisdiction selector only goes down to state/DC, no
county-level topic exists anywhere on the site; the only way to assemble a
county-level roster would be pulling individual state ABC-board pages or
Wikipedia's per-state dry-community lists one at a time, which is the same
directory-scraping-by-another-name this project already ruled out for the
Brewers Association case (Section 8): reported as excluded for a specific,
principled reason, not silently dropped.

**Spatially-informed capture-rate correction**
(`src/breweries/spatial_capture_rate.py`,
`scripts/build_spatial_capture_rate_model.py`): tried and **rejected**
after honest validation, included here specifically because a negative
result from applying the same idea that worked for density is informative
in its own right. Motivation: calibrated states visibly cluster
geographically (the Northeast/Mid-Atlantic corridor is now almost entirely
calibrated), suggesting an uncalibrated state's capture rate might borrow
from its calibrated neighbors the way Section 15's spatial term borrows
from neighboring counties. A US state-adjacency graph was built (hardcoded,
then cross-verified against a TIGER-derived Queen-contiguity graph computed
the same way as the county-level graph: exact match apart from 3 edges
that turned out to be water-boundary corner-touches, not real land
borders, correctly excluded) and used to build a simple neighbor-average/
density-only shrinkage blend (a formal state-level CAR/ICAR model was
considered and rejected as not identifiable at n=23 calibrated states: most have only 1-4 calibrated neighbors, and Texas has zero). Leave-one-out
validation on the 23 calibrated states: the best blend beat the existing
density-only pooled model by only 3.0% MAE in aggregate, and performed
*worse* than the existing model for 9 of 22 states with a calibrated
neighbor (VA, KY, WV, PA, WI, NJ, MA, GA, CT, MI, FL, CA). Capture rate is
dominated by idiosyncratic per-state registry quirks (Section 5.1's table: Missouri's licensee category excluding its own largest breweries, Wyoming's
list only capturing self-distributing brewers, Wisconsin's sweeping in
non-craft manufacturers) that don't transfer to geographic neighbors the
way brewery density's spatial correlation does. **Not adopted**: the
pooled fallback in `capture_rate_model.py` is unchanged; the adjacency
infrastructure is kept as a validated prototype in case the calibrated
count grows enough to revisit this with more per-state neighbors to draw
on.

## 17. Development history and bugs found

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
second-pass investigation of every remaining state: bringing the total to
23. That last piece surfaced a real lesson about this project's own
process, not just the pipeline: several of the calibration agents'
self-reported capture-rate percentages didn't match their own saved
data (most notably Missouri, self-reported as a normal ~54% state but
actually 166%, an inverted ratio, and Massachusetts, self-reported as a
clipped >100% state but actually a normal 83%), caught only because the
central model refit (deliberately kept as a single, non-parallelized step
specifically to avoid this class of error) reproduced all 13 pre-existing
states' values almost exactly, giving a trustworthy baseline against which
the new states' numbers could be checked rather than taken on faith. A
seventh round added two more Model B covariates (unemployment rate,
significant; median rent, null) after investigating and correctly rejecting
a third (county wet/dry status: no usable bulk source), tried and rejected
a geographically-informed capture-rate correction after honest LOSO
validation showed it didn't beat the existing model, and merged the CAR
model's spatial term with Model B's covariates into one BYM2 model: adopted as the project's new headline county ranking after it beat all
three simpler alternatives on held-out log-likelihood, including a real
finding that Model B's covariates alone actually generalize *worse* than
the flat national mean. Real bugs caught across all seven rounds:

- A normal-approximation confidence interval on a skewed Gamma posterior that
  materially understated uncertainty for low-count counties/places (now
  exact Gamma quantiles).
- An ACS suppressed-data sentinel (`-666666666`) that could have silently
  corrupted the adults-21+ denominator for small geographies.
- A FIPS zero-padding strip that would have broken joins if a caller didn't
  defensively re-pad downstream.
- The capture-rate correction model mixing an exposure-weighted aggregate
  ratio with an unweighted regression slope (internally inconsistent by
  construction: now a single weighted regression supplies both), and that
  same model's point estimate silently exceeding 1.0 for very dense counties
  (now hard-capped at 1.0, which turned out to matter for a second, different
  reason once Texas was added; see Key Findings).
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
  New York addresses that Census's own geocoder can't resolve): the
  coordinates column has no comma to split on when every row is `No_Match`,
  so the second column silently doesn't exist. Found while adding New York
  as a calibration state; fixed to reindex the split result so both columns
  always exist regardless of match rate.
- Virginia's independent cities (Fairfax, Franklin, Richmond, Roanoke each
  have both a same-named city and county) collide under TIGER's bare county
  name: the capture-rate model's land-area join used the bare name for
  every state, which would have silently failed to match 8 Virginia
  county/city rows (dropping them from the correction model with no error)
  because Virginia's own data uses the "city"/"County"-suffixed name to
  disambiguate. Fixed by joining against both the bare name and the full
  TIGER `NAMELSAD` for every state, rather than picking one convention. The
  same bare-`NAME`-vs-`NAMELSAD` distinction resurfaced when adding
  data-driven choropleth labels (`map_labels.py`): auto-generated labels
  now use `NAMELSAD` for the same reason.
- Comparing two ACS 5-year vintages (for the population-growth covariate)
  surfaced a cross-vintage geography mismatch, not a code bug: Connecticut
  switched from county-equivalents to "Planning Regions" as its official
  Census geography after 2019, and two Alaska census areas were split out of
  the former Valdez-Cordova Census Area around the same time, so 11
  counties/regions have no earlier-vintage population value and are
  correctly dropped (as `NaN`, not silently zeroed) from that covariate
  rather than mismatched to the wrong geography.
- The interactive map had no `<meta name="viewport">` tag at all: mobile
  browsers default to rendering at a ~980px virtual layout width and
  scaling the whole page down to fit the physical screen when this is
  missing, which also meant every `max-width` mobile media query in the
  stylesheet was silently inert on a real phone regardless of screen size.
  Found from a user-reported screenshot of the map looking "weird" on
  mobile: the header's four toggle-groups were wrapping onto five stacked
  full-width rows, squeezing the map itself down to a sliver at the bottom.
  Fixed by adding the meta tag and reworking the mobile layout (title
  stacked above a single horizontally-scrollable control strip). A second,
  related bug surfaced while verifying the fix: the scrollable control
  strip's container wasn't actually containing its own overflow (a classic
  nested-flexbox gap: a flex item with `overflow-x:auto` still needs an
  explicit width constraint from its container to engage internal
  scrolling instead of just growing past it), which was silently pushing
  the entire page ~250px wider than the viewport. Caught by measuring
  `document.body.scrollWidth` directly rather than trusting a visual
  screenshot alone, since local headless-Chrome testing turned out to have
  its own unrelated viewport-size quirk (a ~500px floor that ignored the
  requested window size) that could easily have been mistaken for the same
  bug or masked it entirely.
- `tourism_estab_per_10k` (tourism establishments per 10,000 residents, a
  Model B covariate) had no protection against small-denominator blowup: a handful of tourism establishments in a county with a few hundred
  residents produces a per-10k ratio with no real-world meaning (Mineral
  County, CO: 12 establishments / 640 people = 164.6, vs. a national mean
  of 3.9). Silently present in Model B since it was first built, never
  caught because Model B's reported tables are always population-floored
  at 50k adults, which happens to exclude every affected county. Surfaced
  when the newly-adopted spatial+covariate model's *unfloored* output fed
  this uncapped value into a log-linear predictor and produced a
  983-breweries-per-100k point estimate for that county, which broke the
  choropleth's legend and would have misled anyone reading the underlying
  data file for that specific county. Fixed by winsorizing at the 99th
  percentile (33 counties capped) in `build_national_county_dataset.py`,
  which also quietly corrects Model B's own fitted values for those
  counties even though Model B was never re-adopted as a standalone
  ranking. A related, initially-suspected-but-ultimately-secondary issue
  was also fixed alongside it: the model's posterior point estimate used
  the mean of the rate samples rather than the median, which for a
  low-exposure county with wide posterior uncertainty in its linear
  predictor can be substantially inflated relative to the median (the
  classic log-normal mean-vs-median gap): switched to the median, standard
  practice in Bayesian small-area disease-mapping for exactly this reason,
  though the winsorization fix turned out to be the one that actually
  mattered most for this specific case.

## 18. Post-publication corrections (the r/dataisbeautiful round)

The population-floored county choropleth was posted publicly and drew
substantial criticism. Most of it was correct. This section records what was
actually wrong, separated from what was pile-on, and what changed in response.
The distinction matters because the two most-repeated complaints ("the data is
bogus", "low-effort slop") were wrong about provenance while pointing at real
defects, and one widely-upvoted complaint was simply mistaken.

### 18.1 The cartography was broken, and it was the reason nobody got further

County polygons came from TIGER/Line, which carries **legal** boundaries.
Those extend across open water wherever a county's jurisdiction does, so the
map filled the Great Lakes, Chesapeake Bay, Long Island Sound and Massachusetts
Bay with solid county colour. Measured against the TIGER attribute table:

| | counties |
|---|---|
| >25% water by area | 248 |
| >50% water by area | 107 |
| Keweenaw County, MI | 91% water |
| Leelanau County, MI | 86% water |

Switching to Census **cartographic boundary** files (`cb_2024_us_county_500k`,
shoreline-clipped) takes Keweenaw from 15,453 km² of painted area to 1,519 km².

This was not merely cosmetic. The Great Lakes counties with the largest water
areas are also small-population counties carrying the *noisiest* rate
estimates, so the bug painted tens of thousands of km² of open lake in the
colour of the least reliable numbers in the dataset. Keweenaw's 47.2/100k
comes from one brewery and 1,809 adults 21+.

Cartographic boundary files are now used for display only
(`tiger.load_cb_counties`). Every spatial join and the Queen contiguity graph
still use TIGER, which is the authoritative boundary — a brewery in a county's
water area still belongs to that county. `fetch_counties`'s glob was tightened
at the same time, because `us_county_*.parquet` matches the new
`us_county_cb_*.parquet` cache **and sorts after it**, which would have made
`load_counties()` silently start returning clipped geometry to every consumer.

### 18.2 The population floor: right problem, wrong instrument

The floor is not paranoia. 1,916 of 3,222 counties (59%) have zero observed
breweries, and unfloored the model paints 238 of them in the "3-6 per 100k" bin
or darker, 14 of them in the top bin — Jackson County CO (0 breweries, 1,121
adults 21+) lands at 30.6/100k, 11th highest in the country. Commenters
arguing the floor should simply be removed were wrong.

But the floor as implemented had two separate problems:

1. **Coverage.** It greyed 2,405 of 3,222 counties — **74.6% of the map** — to
   suppress noise affecting 16.0% of breweries and 15.5% of the adult
   population. (A widely-upvoted comment said "only about a quarter of US
   counties are above 50,000", which is correct and is the same fact stated
   from the other side.)

2. **Rendering.** The "insufficient population" grey `#bfbfbf` has relative
   luminance 0.521; the "3-6 per 100k" bin has luminance 0.516. They were
   optically indistinguishable. Three-quarters of the map was painted at the
   darkness of a mid-scale value, which is why readers reported not being able
   to tell what was being measured.

The deeper issue is that population is only a *proxy* for "how much do we know
about this county", and the model already computes the real answer. So the
headline map now encodes the posterior interval width continuously
(`build_choropleth.py::reliability`): colour fades toward the page as the 95%
interval widens. Data-poor counties wash out gradually instead of being
replaced by a flat grey, and large counties with thin coverage — which the
floor let through at full strength — now fade too. The floored version is
retained as a comparison, with the grey replaced by white-with-hatching so it
sits off the lightness ramp entirely.

Two legend bugs were fixed alongside: bin edges were computed from the full
data column rather than the values actually drawn, so the floored map's top bin
read "15-77" when the highest visible county was Tompkins NY at 16.1 (and
exactly one county occupied that bin); and the "No data" swatch was drawn
despite applying to zero counties in frame, adding a second grey at luminance
0.807 between the two lightest data bins.

### 18.3 The model was biased downward on exactly the counties readers know

Several comments made the same specific, falsifiable claim: St. Louis city
shown in the 3-6/100k band when 26 OBDB entries over 225k adults implies
10-15; Cincinnati (Hamilton County OH) shown far below its 60+ real breweries.
Checked directly, they were right, and the bias was systematic:

- Among the 173 counties with ≥10 observed breweries, the fitted rate came in
  **below** the raw rate 69% of the time, median ratio 0.87.
- Broken out by quintile of raw rate, the top quintile was cut by a median of
  **32%**.
- Nine of 107 well-observed counties had their observed count fall outside
  their own model 95% interval **on the high side** (nominal: ~2.5%), always
  in the same direction.

| county | observed | model-implied expectation | P(obs or more) |
|---|---|---|---|
| Fulton (Atlanta) GA | 28 | 12.0 | <0.001 |
| Charleston SC | 24 | 12.9 | 0.004 |
| Buncombe (Asheville) NC | 33 | 19.7 | 0.004 |
| St. Louis city MO | 20 | 11.1 | 0.010 |
| Deschutes (Bend) OR | 30 | 19.3 | 0.014 |

**Candidate cause (later shown INSUFFICIENT — see the table below): there was
no urbanicity term anywhere in the linear predictor.** The
covariate list ran income, age, college share, tourism, population growth,
unemployment, rent — no density. `density_per_sqmi` was computed upstream in
`build_national_county_dataset.py` and simply never wired in. With no density
covariate, the only component able to express "cities have more breweries per
adult than the farmland around them" was the BYM2 spatial effect, whose entire
purpose is to *smooth* variation toward neighbours. The model was asked to
represent a sharp urban/rural discontinuity using the one term built to erase
it, and it did what it was told: Buncombe is ringed by rural Appalachian
counties, St. Louis city by St. Louis County (1.35/100k), Deschutes by empty
eastern Oregon.

`log_density` is now a covariate (§15.1 spec updated accordingly).

**But the density covariate did not fix the bias, and the follow-up hypothesis
was wrong.** Refitting with `log_density` in, the diagnostics barely moved:

| | published | + density |
|---|---|---|
| fitted below raw rate (n≈170, ≥10 breweries) | 69.4% | 67.8% |
| median model/raw ratio | 0.869 | 0.885 |
| top raw-rate quintile, median cut | 31.6% | 30.2% |
| Fulton GA, observed 28, expected | 12.0 | 12.3 |
| well-observed counties above their own 95% CI | 9/107 | 8/103 |

What it *did* do is move large metros up the ranking — Multnomah County OR
(Portland) and Denver County CO both enter the county top 20 for the first
time — and improve held-out log-likelihood in every size stratum. So it was a
real omission worth fixing, just not the cause of the downward pull.

The obvious next suspect was the spatial prior itself: posterior `rho` = 0.971,
i.e. the spatial effect is almost entirely the structured ICAR component, and
Fulton County is an independently confirmed Gi* **cold spot** (z = −3.01), so
metro Atlanta is surrounded by low-rate neighbours the prior could be pulling
it toward. `scripts/test_spatial_term_urban_bias.py` tests this directly by
fitting the identical covariate + state-FE design with and without the BYM2
term on the same seeded split.

**The result is directionally against the hypothesis, but does NOT refute it.**
Dropping the spatial term makes well-observed counties score worse on held-out
log-likelihood:

| | + BYM2 | no spatial term |
|---|---|---|
| held-out log-lik, mapped (≥50k adults) | **−2.3198** | −2.3715 |
| held-out log-lik, well-observed (≥10 breweries) | **−4.1494** | −4.2987 |
| top raw-rate quintile, median model/raw ratio | **0.648** | 0.494 |
| mean abs log error, well-observed | **0.2747** | 0.4475 |

**Why this is weaker evidence than it first appears.** An earlier version of
`bias_summary()` computed the ratio-based rows over ALL counties from a
train-fold fit, so 141 of 169 counties were IN-SAMPLE. The two models are not
equally flexible in sample: BYM2 gives every county its own free `theta_iid`
plus a neighbour-informed `phi_icar`, while the no-spatial model has ZERO
per-county latent parameters. A model with one free parameter per training
observation fits its own training counties better whatever its spatial prior
is doing, so those rows measured flexibility rather than the mechanism under
test. The function now restricts every diagnostic to the held-out fold; the
ratio figures above should be regenerated before being cited.

What survives cleanly is the held-out log-likelihood — but on **n = 28**
well-observed test counties, with **no confidence interval reported**, and
predictive log-likelihood rewards wider intervals as well as better point
estimates, which is not the same as correcting a one-directional downward bias.

So the honest status is: the BYM2-causes-the-bias hypothesis is **not
supported** by this test, and also **not refuted** by it. Three hypotheses have
now been advanced for the downward bias on well-observed urban counties
(missing urbanicity, spatial smoothing, ordinary partial pooling) and none has
been cleanly established. The bias itself is real and reproducible at
production scale: among 172 counties with >=10 breweries the median model/raw
ratio is 0.884 and the top quintile is cut ~32%.

**Is what remains simply partial pooling?** That is the right null hypothesis,
and in principle a hierarchical model SHOULD report less than the raw rate for
counties sitting far above what their covariates, state and neighbours predict.
But this project's own calibration check argues against calling it settled: for
the well-observed stratum (n=170), `us_county_combined_calibration.csv` reports
**3.53% of counties above their 95% posterior predictive interval and 0.00%
below**, against a nominal ~2.5% in EACH tail. Zero counties below, where ~4.25
are expected, has p ~= 0.014.

Properly-calibrated shrinkage produces symmetric tail exceedance. A
one-directional failure is the same signature used at the top of this section
to DIAGNOSE the problem, so it cannot also be the evidence that the problem is
benign. Either the variance components are too tight for the real heterogeneity
among high-rate counties, or an effect concentrated in that group is missing. Fulton County genuinely is such a county —
28 observed against ~12 predicted from dense-urban covariates plus Georgia's
state baseline.

That makes the reader complaints in §18.3 a **presentation** problem rather
than an estimation one. "You have St Louis in the 3-6 band, that should be
10-15" is arithmetic on the raw count, and it is correct arithmetic; the map
was showing a partially-pooled estimate while being titled "Brewery Density",
which invites exactly that comparison and loses it. The fix is not to
un-shrink the model. It is to stop titling a partially-pooled posterior as if
it were a count, and to ship the raw-rate map alongside it
(`build_map_comparison.py` already renders raw vs. corrected side by side) so
the difference between "what was observed" and "what the model believes" is
visible rather than implied.

Recorded here because the first hypothesis was stated confidently and was
wrong, and the test that killed it is cheap to re-run.

**Why model selection didn't catch it.** Held-out log-likelihood was averaged
over all 3,109 CONUS counties, ~75% of which sit below the map's own
population floor and are never coloured. A spatial prior buys accuracy very
cheaply on counties carrying almost no data, so the pooled average was
dominated by counties no reader looks at — and it selected the specification
that was biased where readers do look. The comparison is now reported
**stratified by county size** (`stratified_holdout_loglik`), alongside a
posterior-predictive calibration check
(`us_county_combined_calibration.csv`) that reports what fraction of counties
fall outside the model's own 95% interval by stratum and direction.

**The state fixed effect is confounded with OBDB coverage.** Measured capture
rate runs from Virginia's 46% to Oregon's 93%. Without a capture-rate offset
the state FE cannot separate "this state has few breweries" from "OBDB covers
this state badly"; it fits the product. Georgia is the clean illustration:
capture rate 0.476, and its state FE moves +0.72 when the same model is fit to
corrected counts — approximately log(1/0.476), i.e. essentially all of that
state effect was coverage, not beer. `fit_nb_model` now accepts a
`log_capture_rate` offset that de-confounds the two. It is **off** by default,
because turning it on changes the published quantity from an OBDB-observed
rate to a true-brewery rate, which is an editorial decision rather than a bug
fix; the variant is fit and reported in the holdout table either way.

### 18.4 Data quality: real holes, and one complaint that was wrong

`brewery_type` filtering removes correctly-typed cideries and meaderies, but
OBDB is crowdsourced and the type field is frequently wrong. 113 records that
pass the type filter name a competing beverage category in their own name, 31 of
them with no brewing token at all.
A reader flagged Leelanau County, MI — Michigan wine country — ranking near the
top on five records; two of them ("Green Bird Cellars and Organic Farms", a
winery typed `micro`; "Sugarfoot Saloon", a bar) are not breweries. At 18,638
adults 21+ those two records moved the county's raw rate by 10.7 per 100k.
Leelanau now counts 3.

Name matching alone cannot decide this — "Sapwood Cellars", "Cellar West
Artisan Ales", "Raney Cellars" and "Monk's Cellar" are all real breweries — so
`obdb_hygiene` uses the regex only to build a review queue and drops solely
from an explicit, reasoned table. Unreviewed candidates are reported and kept.

Other findings from the same pass (`data/processed/obdb_hygiene_report.csv`):

| issue | records |
|---|---|
| ungeocoded, previously dropped by a silent `dropna()` | 218 (3.2%) |
| duplicate entries at identical coordinates | 76 |
| reviewed non-breweries | 24 |
| county misassigned by street-name collision | 7 |

The misassignment check is worth recording because the obvious version of it
does not work. Comparing a record's stated `city` to the TIGER place it landed
in fires on 412 records, nearly all correct geocodes of neighbourhood names
(Van Nuys → Los Angeles, La Jolla → San Diego). Narrowing to "stated city is an
incorporated place that lies outside the assigned county" gives 24, which still
mixes two populations — cleanly separated by distance from the record to its
own stated city, with a gap in the data from 10.8 km to 26.8 km and nothing in
between. Below the gap: mailing addresses in unincorporated areas, where the
geocode is right (Mt. Carmel Brewing is genuinely in Clermont County, 6.0 km
outside Cincinnati). Above it: street-name collisions, every one of which turns
out to be the same trap — a town and a *different* county sharing a name.
Deer Lodge (town in Powell County), Blue Earth (town in Faribault County),
Sheridan MT (town in Madison County, 687 km from Sheridan County), and the case
that prompted the check: Quarter Barrel Brewery and Pub, 103 Main St, Hamilton
OH, geocoded to a Main St in Cincinnati and counted in Hamilton *County*.

Duplicate collapse keys on coordinates, never on name. Same-brand records at
genuinely different addresses (E.J. Phair in Alamo, Concord and Pittsburg CA)
are three real premises, and whether satellite taprooms should count as
separate breweries is the definitional question already addressed in §6 — not
a data error.

**The one complaint that was wrong:** a reader asserted OBDB listed only a
"paper brewery" in Lewis County, WA. It lists three real ones — Dick's Brewing,
Jones Creek Brewing, and McMenamins Olympic Club. The count there was correct.

### 18.5 Labelling

Label offsets are small (≤18pt), but a long label's *text* still extends far
from its own dot — "Hampshire County, MA" is ~150px wide at 7.5pt, which in
New England spans several counties. With nothing connecting text to dot,
readers attributed labels to the wrong county (reported for Hampshire County MA,
Whatcom County WA, and the Burlington/Grafton pair). `map_labels.place_labels`
now draws a leader line from each dot to its text.

The city-vs-county inconsistency readers noticed ("Bend, OR" beside "Tompkins
County, NY") is real but deliberate: the first is a hand-curated face-validity
anchor, the second is auto-generated from the ranking. Documented rather than
changed, since the anchors exist precisely to be recognisable place names.

### 18.6 Claims in the post that overstated the work

Two, recorded so they are not repeated:

- The post said OBDB undercounts by "7–38%". The measured range is **7–54%**
  (capture rate 93% in Oregon to 46% in Virginia). The worst case was
  understated by a wide margin.
- The post said the model adjusted for "state alcohol regulations". There is no
  alcohol-regulation variable in the model. There is a state fixed effect,
  which absorbs everything that varies at state level — regulation, yes, but
  also OBDB coverage (see §18.3). Calling it a regulation adjustment claimed a
  specificity the model does not have.

### 18.7 Three critiques that were missed on the first pass

Found only on a second, systematic pass back through the thread, which is
itself the lesson: the loudest complaint (the Great Lakes) crowded out
quieter ones that were just as actionable.

**State boundaries were never drawn.** A reader asked directly to "make the
borders between states darker". The map had county edges at a uniform
hairline and nothing else, which is also the mechanism behind a separate
complaint that the map was "nearly unreadable across much of the midwest":
with no landmark a reader cannot find a state they know and orient from it.
`build_choropleth.py` now dissolves counties by STATEFP and draws state
outlines over the fill.

**There was no absolute-count output anywhere in the project.** Two readers
independently asked for total brewery counts rather than a per-capita
adjustment — "gives you little to no idea how many actual breweries there are
in any given place". They were right, and every county-level rendering the
project produced was a modelled rate. `build_count_map()` now ships
`us_brewery_count_map.png`: raw OBDB counts, no model, no denominator, no
correction.

Proportional SYMBOLS rather than a choropleth, because a count filled across a
polygon is decoded as density-by-area — San Bernardino County would carry more
visual weight than Manhattan on area alone. Symbol *area* scales with count,
which is the encoding readers actually decode correctly.

**The title was wrong, and that was the sharpest critique in the thread.**
"This isn't 'brewery density', it's 'brewery density relative to county
population'." Correct, and r/dataisbeautiful's rule 7 requires titles to
describe the data plainly. The headline is now "Breweries per 100,000 Adults
21+, by US County", which says what the quantity is instead of naming a
concept the quantity only approximates.

### 18.8 Known-open items

Recorded rather than quietly dropped:

- **City vs. county labels are mixed** ("Bend, OR" beside "Tompkins County,
  NY"). Deliberate — the first is a hand-curated face-validity anchor, the
  second is auto-generated from the ranking — but two readers flagged it and
  it will draw the same comment again.
- **County is arguably the wrong areal unit.** State outlines help legibility,
  but the underlying objection stands. CBSA-level output already exists and is
  the better headline geography for a general audience.
- **Salem County NJ shows zero breweries** because OBDB lists none, while at
  least three exist. Not reachable by any pipeline fix; it is the state-level
  coverage gap made concrete, and the reason headline outputs carry the
  capture-rate caveat.
- **Only some high-rate counties are labelled.** Label placement is
  collision-aware with a cap (`MAX_AUTO_LABELS`), so a dense cluster silently
  loses labels to its neighbours. Readable, but not a documented rule from the
  reader's side.

### 18.9 Latent capture rate: the confounding, and why it is not fitted by MCMC

`CAPTURE_RATE_OFFSET` de-confounds the state fixed effect from OBDB coverage
by putting log(capture_rate) in the offset. It is left OFF, because an offset
asserts the capture rate with zero error and it is not known with zero error:
1,475 counties (29.6% of US adults 21+) sit in states whose rate is a WLS
extrapolation with a ~3.6x interval, not a measurement.

That understatement is worse than it used to be, because the headline map now
fades counties by interval width. Too-narrow intervals no longer just shrink a
number in a table — they make a county look MORE confidently drawn, and the
least-certain capture rates are exactly the uncalibrated states. A fixed offset
would render the worst-covered states as the most confident ones.

`src/breweries/latent_capture_rate.py` treats the capture rate as latent with
its calibration prior instead. Measured effect on interval width (log scale):

| capture source | counties | observed | fixed offset | latent |
|---|---|---|---|---|
| calibrated | 1,747 | 1.471 | 1.471 | 1.506 |
| pooled extrapolation | 1,362 | 1.557 | 1.557 | **1.941** |

The fixed offset's width is *identical* to the uncorrected width — dividing by
a constant shifts an interval without widening it. The latent treatment widens
pooled states by 25% against 2% for calibrated ones.

**The data cannot identify the capture rate at all.** Writing the model out,
log(c_s) and the state dummy's coefficient are additively confounded — the
likelihood only sees their sum, and nothing in a brewery count distinguishes
"few breweries here" from "few breweries *listed* here". So the split is driven
entirely by the external calibration prior, not learned. The sensible
parameterization is therefore the orthogonal one: `state_total` (= state FE +
log c) is what the data identify, which is exactly the model already fitted,
and true-scale rates follow by dividing the existing posterior by DRAWS from
the capture prior.

The ANALYTIC argument is decisive on its own: the likelihood is invariant under
`beta_state_s -> beta_state_s + k`, `log(c_s) -> log(c_s) - k`, provable by
inspection with no simulation needed.
`scripts/fit_latent_capture_rate_model.py` attempts empirical confirmation on
top of it:

- **Test 1, identification.** NOTE: the first version of this test was
  CIRCULAR and its reported numbers (mean-shift 0.013 prior sds, sd ratio
  0.999) must be disregarded. It fitted the orthogonal parameterization, in
  which `log_capture` appears only in a Deterministic and never in the
  likelihood, so its posterior was forced to equal its prior BY CONSTRUCTION.
  The test measured only that PyMC samples a parameter carrying no likelihood
  term: a tautology about the model graph, not evidence about the data. It now
  fits the NAIVE parameterization, with a free state effect and `log_capture`
  both entering the linear predictor additively, so the likelihood CAN move
  `log_capture` if the data carry information about it. Rerun before citing.
- **Test 2, equivalence.** Convolution vs. joint fit on the same observed
  rates: median |relative difference| **0.0024** (point estimate), 0.0056 and
  0.0069 on the interval bounds; correlations >= 0.9999.

The validation fit deliberately omits the BYM2 term. The confounding under
test is a property of the state-level linear predictor, and a per-county
spatial random effect carries no state-level location, so dropping it tests the
same question while removing 6,218 of ~6,280 stored parameters per draw — which
is what makes the test runnable on a 16GB machine at all.

Consequence for the headline: the observed-scale posterior is untouched, so the
map does not move. Only the true-scale quantity gains the uncertainty it should
always have carried.

### 18.10 Puerto Rico rows in the "3,222 county" universe (open defect)

Surfaced by an audit of this round, not by the reader feedback, and NOT fixed
here because fixing it changes headline denominators and belongs in its own
change.

`data/processed/us_county_analysis.parquet` -- the 3,222-row universe every
county-level number in this project is drawn from -- contains **78 Puerto Rico
municipios**, all with `obdb_count = 0`, 2.59M adults 21+, and
**`state_abbr = NaN`**. Section 3 of this memo states the opposite
("territories not present"), so the documentation and the data disagree.

Consequences actually observed:

- `us_county_brewery_deserts.csv` ranks **San Juan Municipio, PR at #5
  nationally** (272,420 adults, 0 breweries). The README's prose summary of
  that same ranking silently skips ranks 5 and 6, so the narrative and the
  file it describes do not match.
- `state_rollup_table.csv` correctly excludes PR (51 rows = 50 states + DC),
  so the exclusion is applied inconsistently across outputs.
- PR is absent from the CONUS modelling universe (3,109 counties) regardless,
  so no *model* estimate is affected -- this is a descriptive-output and
  documentation problem, not an inference one.

The right fix is to decide explicitly whether territories are in scope, apply
that decision in ONE place upstream (`build_national_county_dataset.py`), and
restate Section 3 to match. Anything else leaves the two in conflict again
after the next refresh.

### 18.11 How much of the n=28 problem is the sample size?

Section 18.3 flags that the surviving BYM2-vs-no-spatial evidence rests on 28
held-out well-observed counties with no confidence interval. It is worth
separating two distinct complaints, because only one of them is likely to be
binding.

Per-county held-out log predictive density has sd ~0.511 nats across
well-observed counties (NB at the fitted rates, alpha~0.2). For a PAIRED
comparison the relevant spread is `sd * sqrt(2*(1-rho))`, where rho is the
county-by-county correlation between the two models' log-liks:

| rho | sd of difference | SE (n=28) | 95% half-width | vs. observed +0.1494 |
|---|---|---|---|---|
| 0.99 | 0.072 | 0.0137 | 0.027 | clears 0 |
| 0.95 | 0.162 | 0.0305 | 0.060 | clears 0 |
| 0.90 | 0.228 | 0.0432 | 0.085 | clears 0 |
| 0.80 | 0.323 | 0.0611 | 0.120 | clears 0 |
| 0.50 | 0.511 | 0.0965 | 0.189 | includes 0 |
| 0.00 | 0.722 | 0.1365 | 0.268 | includes 0 |

The break-even is around rho ~ 0.65. Two models sharing the same covariates,
the same state fixed effects and the same data, differing only by a spatial
random effect, would ordinarily agree far above that — so n=28 is PROBABLY
adequate, and the small sample is probably not what undermines the claim.

What actually undermined it was the train/test contamination (Section 18.3):
that was a bias in the estimate, not merely noise around it, and no sample
size fixes it.

But "probably adequate" is not "verified", and rho is precisely the quantity
the old aggregate-only reporting threw away. `nb_holdout_loglik_per_county()`
and `paired_bootstrap_ci()` now retain and use it, so the next run answers
this directly instead of inviting this estimate. The table above is a bound on
what can be said WITHOUT that run, not a substitute for it.

### 18.12 Scored against external truth, the modelling layer is the wrong place to have spent the effort

Every model-selection decision in this project was made on held-out
log-likelihood — i.e. on how well a model predicts OBDB's own counts. That is
a real quantity, but it is not accuracy. The 18 calibrated states with
trustworthy registries allow the comparison that was never run: how close is
each estimator to the number of breweries that actually exist?

| estimator | median abs. error | median signed | closest in |
|---|---|---|---|
| raw OBDB count | 34.6% | −34.6% | 1 / 18 |
| Model A (empirical-Bayes shrinkage) | 29.9% | −29.9% | 1 / 18 |
| **Combined BYM2 (the adopted headline model)** | **33.7%** | −33.7% | **1 / 18** |
| **OBDB ∪ OSM union, no model at all** | **13.9%** | −11.7% | **15 / 18** |

Three things follow, and none of them is comfortable.

**1. The union beats the adopted model by 20 percentage points, with no model
at all.** Fixing the measurement was worth roughly twenty times more than
every covariate, state effect, spatial prior and posterior in the project
combined. This is the single most important number in this memo.

**2. The adopted model is WORSE against truth than the simpler model it
replaced.** Model A — closed-form empirical-Bayes shrinkage, no covariates, no
spatial term, no MCMC — lands at 29.9% versus the combined model's 33.7%, and
is closer in 7 of 18 states. Section 15.3 adopted the combined model because it
"wins outright" on held-out log-likelihood. It wins at predicting OBDB. It
loses at being right.

**3. The adoption evidence was measuring partly leakage.** Section 18.3
established that the random 80/20 split leaves 99.7% of held-out counties with
a neighbour in training and 80.8% of their neighbours in training. A spatial
model scored on that split is being rewarded for information it was handed.
The headline choice should be treated as provisional, not settled.

WHAT THIS DOES AND DOES NOT ESTABLISH
-------------------------------------
It does not show the model is useless. Registry states skew larger, more urban
and better-observed — exactly the counties where a smoothing model has least
work to do. The model's actual purpose is variance reduction in small-count
counties (stopping a 0-brewery, 640-adult county reporting 983/100k, Section
15.4), and no registry exists to score that population. This comparison
structurally cannot see the model's best case.

But it does establish that the model is not earning its place *for the
quantity the map presents*, that a simpler estimator currently beats it on the
only external check available, and that the ranking of priorities was
backwards: measurement error was ~35x model error, and the effort went almost
entirely into the model.

RECOMMENDED CONSEQUENCE
-----------------------
Treat the count map (now union-backed) as the headline artifact, and the
modelled rate map as a labelled smoothing layer rather than the primary
ranking, until a state-block holdout re-runs the adoption decision without
leakage. See 18.13 for the order in which the union has to be wired into the
model — capture rates must be re-derived on the union numerator FIRST, or the
correction double-counts.

### 18.13 Wiring the union into the model: the order is not optional

`us_county_union_counts.parquet` exists and only the count map consumes it.
The model still reads `obdb_count` from `us_county_analysis.parquet`. Swapping
that to `union_count` looks like a one-line change. It is not, because the
capture rate and the brewery count are not independent: every calibrated
capture rate in `capture_rate_model.py` is defined as OBDB count / licensee
count.

Correct order:

1. Re-derive capture rates with the UNION as the numerator (`build_capture_rate_model.py`,
   same 23 states, same WLS, different numerator column). Cheap, statsmodels,
   no MCMC. `us_union_state_validation.csv`'s `capture_after` column is
   effectively this already computed for the 23 states; it just has not been
   fed back into a re-fit pooled model.
2. Repoint `CALIBRATED_STATE_CAPTURE_RATES` / `POOLED_CAPTURE_RATE` at the new fit.
3. Only then repoint the model's `y` at `union_count` and refit.

What breaks if step 3 runs first:

- With `CAPTURE_RATE_OFFSET = False` (the default), nothing breaks numerically.
  The model just fits a larger, less biased count. This is the safe partial step.
- With the offset ON, it breaks badly. The offset would divide union counts by
  a capture rate still measuring OBDB's gap. Georgia: OBDB capture 0.476, so
  the offset applies a ~2.1x correction to a numerator that has already
  recovered part of that gap through OSM — a double correction, inflating the
  true-rate estimate past what either source supports.

### 18.14 Calibrated capture rates claimed zero uncertainty regardless of registry size (FIXED)

`correction_factor()` returns `ci_low=None, ci_high=None` for all 23 calibrated
states, on the rationale that a measured rate carries no extrapolation
uncertainty. It still carries SAMPLING uncertainty, and several registries are
small: **DC has 14 licensees, WY 28, WV 33, NE 66, CT 107.** A rate estimated
against 14 licensees is not exact.

This acquired a live consequence in 18.9: the headline map now fades counties
by interval width, and `latent_capture_rate.CALIBRATED_LOG_SD` applies one flat
0.10 to every calibrated state regardless of n. So a 14-licensee state renders
as among the most confidently drawn on the map purely because it has a registry
at all — the opposite of what its sample size supports.

FIXED. `latent_capture_rate.calibrated_log_sds()` now derives each calibrated
state's log-sd from a Jeffreys interval on
`obdb_count ~ Binomial(licensee_count, capture_rate)`:

| state | licensees | log-sd (was a flat 0.10) |
|---|---|---|
| DC | 14 | 0.202 |
| VA | 413 | 0.050 |
| CA | 1,270 | 0.030 (floored) |

A floor of 0.03 applies, because binomial sampling is not the only error in a
capture rate -- record linkage, registry currency and license category all
contribute, so California's n=1,270 should not read as 0.3% certain.

Five states are deliberately NOT given a binomial interval: WY, MO, TX, IL and
WV, each documented in `capture_rate_model`'s docstring as a registry that
structurally UNDERCOUNTS (self-distribution exemptions, excluded license
categories, cumulative exports, a stale PDF). Their reference is wrong, not
their sample, so a tight sampling interval would claim near-certainty about the
least trustworthy ground truth in the project. They take the pooled width
(0.326).

That set is named EXPLICITLY rather than inferred from
`obdb_count > licensee_count`, because the inference is off by one state: West
Virginia's ratio is exactly 1.000, so `k > n` is False and WV was receiving the
tightest interval in the whole table (floored to 0.030) off a 33-licensee,
~13-month-stale PDF snapshot -- drawn as better determined than California's
1,270-licensee sample. `k > n` is retained as a backstop so a future refresh
that pushes another state over 1.0 is caught before anyone updates the list.

California and Virginia are deliberately NOT in that set. Their registries
count licences or premises rather than businesses, which makes the reference
too LARGE, not too small -- deflating their measured capture rate rather than
inflating it. A binomial interval on the observed ratio is still meaningful
there, though it does imply both are probably over-corrected, which is a
separate open question.

Effect on the published output: the calibrated stratum's median true-rate
interval widens from 1.506 to 1.552 (log scale), i.e. small-registry states
stop being drawn as more confident than their sample size supports.

### 18.15 Systematic CBP validation: an independent national corroboration

County Business Patterns (NAICS 312120) was previously used for two anecdotes.
It is now checked systematically, because it is the only INDEPENDENT NATIONAL
reference this project has: 497 counties with a non-suppressed establishment
count, covering 66% of US adults 21+.

Its value is that its bias is ORTHOGONAL to OBDB's. OBDB and OSM are both
crowdsourced and miss the same kinds of brewery, which is why capture-recapture
between them failed so badly (Section 5.3; the three-source attempt
overestimated Colorado by 10x). CBP fails a different way — brewpubs filed
under NAICS 722511 instead of 312120 — so agreement is meaningful evidence and
disagreement is a targeted signal.

| source | Spearman ρ vs CBP | median ratio vs CBP |
|---|---|---|
| OBDB only | 0.8165 | 1.077 |
| **OBDB ∪ OSM union** | **0.8480** | 1.350 |
| model-implied count | 0.7960 | 0.978 |

**The union ranks counties more like CBP than OBDB alone does, and the model
ranks them least like CBP.** That is independent corroboration of 18.12, from a
national source rather than 18 registry states, and it was arrived at a
completely different way. The ordering is the same in both tests: union first,
raw OBDB second, model last.

The median ratio above 1.0 is expected and is not evidence of over-counting —
CBP misses brewpubs by construction, so a crowdsourced source legitimately
lists more. Rank correlation is the informative column.

**Divergence >3x: 16 of 497 counties (3.2%).** The actionable direction is the
four where CBP exceeds our union, since CBP is administrative and those are
counties both crowdsourced sources missed:

| county | OBDB | union | CBP |
|---|---|---|---|
| Beaufort County, NC | 0 | 0 | 3 |
| Parker County, TX | 0 | 4 | 4 |
| Fayette County, GA | 1 | 1 | 4 |
| Walton County, FL | 2 | 2 | 7 |

Beaufort NC and Parker TX are the Salem County NJ pattern again: a county where
we report zero and an administrative source reports several.

WHAT THIS CANNOT DO
-------------------
CBP suppression is not random — small cells are withheld, so CBP is absent
exactly where the model's uncertainty is largest. It validates the TOP of the
distribution, which is where the documented bias lives, and is silent about the
59%-zero tail. It is a corroboration exercise, not a second ground truth.

### 18.16 TTB brewery data is not obtainable — a closed door, recorded so it is not reopened

A reader on the original post objected that "if you really want to count
breweries in a specific county, the TTB will be the ultimate arbiter there. No
brewer's notice, no brewery." That is correct about authority — TTB is the
federal regulator, a Brewer's Notice is the actual legal precondition for
brewing, and a TTB list would have been a single national reference with one
uniform definition, replacing 23 heterogeneous state registries of which only
18 are trustworthy.

**The data is not published, and cannot be.** TTB's List of Permittees covers
basic permits, Puerto Rico basic permits, importers, wholesalers, spirits
producers/bottlers and wine producers/blenders. It does not cover brewers.
TTB's own statement:

> "The Internal Revenue Code (Section 6103) protects taxpayer records from
> public disclosure, so we do not publish lists of brewers, industrial alcohol
> producers and users, or tobacco permit holders."

The reason is structural rather than administrative. Wineries and distilleries
hold FAA Act *basic permits*, which are disclosable. Brewers operate under a
Brewer's Notice issued under the Internal Revenue Code, which makes the
registration a protected taxpayer record. So the one federal source that would
settle brewery counts nationally is the one category of alcohol producer whose
registry is statutorily confidential — and no amount of engineering effort,
FOIA request or budget changes that.

CONSEQUENCES FOR THIS PROJECT
-----------------------------
- **The 23-state registry ceiling is permanent, not a matter of effort.**
  Expanding calibration means adding more state registries one at a time, each
  with its own definition and staleness; there is no national shortcut.
- **County Business Patterns is therefore the only independent NATIONAL
  reference available** (Section 18.15), and its limits — non-random
  suppression of small counties, brewpub misclassification under NAICS 722511
  — are the limits of national validation for this project, not a temporary
  state of affairs.
- **The closure/staleness bias stays unquantified.** OBDB carries no date or
  status field, and TTB's dated, revocable permits would have been the natural
  way to detect listings that are stale rather than missing. Every correction
  this project applies pushes counts UP (capture rate below 1, the OSM union
  adding records) while an opposite-direction error from closed-but-still-listed
  breweries remains unmeasured. That asymmetry is now a known, and currently
  unclosable, limitation rather than an oversight.

This was proposed in this session as the highest-value remaining improvement.
It was wrong: the option does not exist. Recorded here so the same
recommendation is not made again.

### 18.17 CBP cannot extend calibration beyond the 23 states — but the union can

Section 18.16 established that the 23-state registry ceiling is permanent,
since TTB brewery data is statutorily confidential. CBP covers 497 counties
nationally, including 151 counties across 27 of the 28 uncalibrated states, so
the obvious question is whether it can supply a state-specific capture estimate
where no registry exists — replacing the single national constant
(`POOLED_CAPTURE_RATE = 0.610`) that 29.6% of US adults currently rely on.

**It cannot.** Leave-one-state-out across the 18 trustworthy registry states,
predicting each state's true brewery count:

| method | median abs. error | 90th pct | closest in |
|---|---|---|---|
| pooled national constant (current fallback) | 22.3% | 42.3% | 8 / 18 |
| **CBP × ratio learned from other states** | **28.2%** | 53.0% | 2 / 18 |
| **union × ratio learned from other states** | **14.7%** | **30.4%** | 8 / 18 |

CBP is *worse* than the constant it would replace. The reason is visible in the
state-level ratios: registry truth / CBP ranges from 1.09 (NJ) to 2.73 (NE),
a coefficient of variation of 0.27. That spread is brewpub misclassification
varying by state — states differ in brewpub share, and CBP files brewpubs under
NAICS 722511 rather than 312120. A national CBP-to-truth factor therefore
transports badly, and knowing a state's CBP count tells you less about its true
brewery population than knowing nothing and using the national average.

This is a genuine negative result, recorded so the idea is not revisited: CBP's
value is corroboration (Section 18.15), not calibration.

**The same test found something useful, though.** Scaling the OBDB ∪ OSM union
by a learned factor predicts state truth at 14.7% median error against the
current fallback's 22.3% — a third better, and with a much tighter tail (30.4%
vs 42.3% at the 90th percentile). That is consistent with 18.12 and 18.15: the
union is simply closer to truth than OBDB is, so a fallback built on it starts
from a better place.

RECOMMENDED, NOT YET APPLIED
----------------------------
Replacing the pooled fallback for the 28 uncalibrated states with a
union-based estimate is the natural next change. It is deliberately NOT made
here, because capture rates and brewery counts are not independent (Section
18.13): changing the fallback changes `capture_rate`, which changes
`obdb_corrected`, which feeds outputs that would then disagree with a model
still fitted on OBDB-only counts. The correct sequence is to re-derive capture
rates on the union numerator and refit in one pass, not to change one end of
the chain mid-stream.
