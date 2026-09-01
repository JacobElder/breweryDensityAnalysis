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

Two more national-ranking cases got direct, independent confirmation during
the state-expansion rounds rather than remaining unverified: Flagstaff
(Coconino County, AZ) — a national top-20 per-capita result — was confirmed
by all three independent sources available for Arizona (OBDB=12, OSM=9,
CBP=5, all agreeing there's a real cluster, not a single-source artifact) at
nearly 3x the state's second-place county. Richmond city, VA — flagged by
Model B's residual ranking (Section 9) — was confirmed to have a genuinely
outsized raw rate (10.2 per 100k adults 21+) once Virginia's own ABC data
was available, correctly distinct from the much larger, rural Richmond
*County* it shares a bare TIGER name with.

## 5. Coverage error — the honest section

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
dataset), and VA (ABC bulk Excel export) — see
`src/breweries/sources/{nc_abc,mi_lara,co_liquor,or_olcc,wa_liquor,tx_liquor,
ga_dor,wi_dor,pa_liquor,il_liquor,ca_abc,ny_sla,va_abc}.py`.

A second round added ten more states/DC: KY (ABC BELLE Portal JSON export),
FL (DBPR weekly public-records CSV), CT (Socrata "Liquor Permits" dataset,
credential-prefix filtered), MA (ABCC "Active State Licenses" XLSX,
geocoded), MO (ATC "Primary Alcohol License" Socrata dataset), NE (NLCC
"Active License Roster" Excel export), NJ (ABC monthly wholesale/state-issued
licensee listing, geocoded), WV (ABCA "Resident Brewers" PDF list), WY
(state "Malt Beverage Wholesaler List" PDF), and DC (ABRA opendata.dc.gov
GIS FeatureServer layer) — see
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
field needed for rigorous inclusion, or — most commonly — interactive-only
search tools with no export).

**A methodological note on this round's own process.** Ten states were
investigated in parallel by five separate agent batches, each self-reporting
a computed capture rate. Before trusting those numbers, a single centralized
refit (`scripts/build_capture_rate_model.py`, deliberately kept as one
non-parallelized step to avoid exactly this class of error) reproduced all
13 pre-existing states' capture rates to within rounding of their established
values — a strong signal the refit methodology itself was sound. Checking
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
lesson is procedural, not statistical — self-reported summary numbers from
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
  authorizations attached to a retail permit — the reference itself
  undercounts, not a red flag about OBDB.
- **WI (117%)**: WI DOR's "Brewery" permit category sweeps in some non-craft
  manufacturers (e.g. Anheuser-Busch's Milwaukee plant) that BA's craft-only
  definition excludes — the reference measures a broader population than
  "craft breweries."
- **PA (110%)**: plausibly BA's own count lagging recent openings/closures,
  similar to the pattern in other license-based states, but not independently
  confirmed beyond that hypothesis.
- **CA (135%)**: ABC's export counts *licenses*, and 216 distinct licensees
  hold more than one Type 01/23/75 license (386 "extra" licenses beyond
  one-per-company — e.g. Firestone Walker holds 17), plus some Type-01
  holders are large non-craft operations (e.g. wineries with an incidental
  beer-manufacturer license) that BA's craft definition excludes.
- **VA (120%)**: ABC's export similarly counts licensed premises rather than
  brands; several operators hold multiple Virginia sites (one holds 5).
- **MO (50%, licensee/BA)**: MO ATC's "Microbrewery" license category
  structurally excludes the state's own large/regional breweries
  (Anheuser-Busch, Boulevard Brewing hold no license in this category),
  which pulls the licensee count well below BA's craft-inclusive total —
  and, more consequentially, drives the OBDB *capture* rate the opposite
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
breweries) — see above for each. All clipped to 100% since a capture rate
cannot exceed a true population by definition — see 5.2.
‡ WV's raw ratio computes to exactly 100.0% — at the boundary rather than
clearly over it. ABCA's list is a dated PDF snapshot (~13 months stale as of
this fetch) rather than a live query, so this isn't read as meaningfully
different from the >100% states above.

CBP (NAICS 312120) is worse than OBDB in every calibration state where
directly compared (27-54% capture in the original 4 states) — consistent
with its known brewpub-misclassification problem (brewpubs often file under
NAICS 722511, restaurants, not 312120).

**A cross-check worth noting**: South Carolina (no state registry, so not in
the tables above) shows OBDB's Charleston County count (24) matching CBP's
independently-collected federal establishment count (24) exactly — evidence
that OBDB's raw count for at least this specific, previously-flagged county
(see Section 9's residual ranking) is not itself inflated, even though the
county's *residual* ranking is one of the less LOSO-stable ones (Section
9.1). Virginia's data provides a similar direct check for Richmond city,
another flagged county: it genuinely has an outsized raw rate (10.2 per 100k
adults 21+), correctly distinct from the much larger, rural Richmond
*County* — Virginia's independent cities share bare TIGER names with a
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
that state's empirical rate (capped at 1.0 — see the TX/IL/WV/WY/MO cases
above); every other county uses a pooled 61.0% baseline rate with a
**deliberately wide** 95% interval — 32.2% to 100% (the uncapped upper
bound is 115.5%, clipped down to 100% for the same reason as the
calibrated-state cap) — derived from the between-state variance, not
tightened by density. That width is the honest answer, not a bug: 23
states still cannot support a tight one — and, per Section 13.2, the
interval has if anything gotten *more* justified for staying wide as more
states were added, not less, since the calibrated states keep turning up
more extreme values than the pooled model can express.

**On the 61.0% figure specifically** — this is *not* the exposure-weighted
aggregate ratio (`obdb_count.sum()/licensee_count.sum()` across the pooled
sample, which computes to ~71% with the 23-state sample). Those are two
different quantities: the aggregate is pulled up by a handful of large,
high-capture counties (Buncombe, Mecklenburg, Wake, Denver), while 61.0% is
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
- **No Brewers Association bulk download or directory scrape.** Thirteen
  single state-total lookups were made (NC, MI, CO, OR, WA, TX, GA, WI, PA,
  IL, CA, NY, VA), each dated and cited inline in the relevant build script,
  per the project's explicit "no bulk download/scrape" constraint.
- **No Mississippi bulk alcohol-license data.** Checked thoroughly: the MS
  Dept. of Revenue's pages describe the Manufacturer/Brewpub permit
  categories but publish no roster; the only public lookup is
  `tap.dor.ms.gov`'s interactive, session-based per-record search — the same
  category of tool NC's individual-permit search fell into above, and not
  scripted around for the same reason. No Mississippi open-data portal
  exists (confirmed absent, unlike CO/TX/GA's Socrata portals). A clean "no,"
  not a gap in effort — Mississippi is not a calibration state.
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
  reason.** Tennessee's ABC does not regulate ordinary beer — only
  high-gravity beer (≥8% ABW) — so ordinary brewery permitting is entirely
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
| Median age | +0.046/year | Older-median-age counties have (slightly) more breweries — plausibly reflects established, higher-income communities rather than a youth effect |
| College enrollment share | +7.2 | Strong college-town effect, as expected |
| Tourism establishments per 10k | +0.029 | Tourism effect present and significant, as expected — this is a covariate being conditioned on, not a nuisance to explain away |
| 5-yr county population growth (%, 2019→2024 ACS vintage comparison) | +0.000895 (p=0.853) | **Clean null** — no detectable effect once the other covariates and state FE are already in the model. Chosen specifically because it varies *within* a state (a state-level-only covariate like an excise tax rate would be perfectly collinear with `C(state_abbr)` and inestimable). Top-20 residual ranking essentially unchanged after adding it (two adjacent-rank swaps only). 11 additional counties dropped from the model versus the pre-covariate baseline, all due to a genuine cross-vintage geography mismatch (Connecticut's 2019→2024 switch from counties to Planning Regions; two Alaska census areas split out of the former Valdez-Cordova Census Area), not missing-data suppression — correctly left as `NaN` and dropped rather than mismatched. |

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

**Update — round 3 (fresh false-positive measurement) and round 4 (targeted
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
   Confirmed case: "Cellarmaker Brewing Company" (San Francisco) — OBDB's
   point is 3,579.77m from the nearer of two OSM "Cellarmaker" nodes (the
   second, at 12,266.33m, is a genuinely different reference and correctly
   stays unmatched). Both exceed the 300m default `max_distance_m`.
2. **Near-threshold rename/abbreviation pairs.** E.g. "Wild Heaven Beer"
   (GA) vs. OBDB's "Wild Heaven Craft Beers" — 62.9m apart, name score 64.7,
   just under the default 65 `name_threshold`.
3. **No-address OBDB records.** Some `planning`-status listings have no
   street address at all, so `fill_missing_coords()` can never geocode them
   — these can never match regardless of true proximity. Structurally
   different from causes 1-2: no matching-threshold adjustment can fix a
   record with no coordinates to compare.

Round 4 addressed causes 1-2 with an opt-in second-pass fallback in
`match_records()` (`fallback_stages` parameter, default `None` so all
existing callers/tests are unaffected). Records still unmatched after the
primary (300m, score≥65) pass get two additional Hungarian-optimal
assignment attempts, each trading exactly one constraint for slack while
holding the other tight — deliberately conservative, not just "loosen
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
OBDB/OSM coordinates are 9.5km apart — beyond even the widened 5km cap,
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
(3,109 of them — Alaska, Hawaii, and territories excluded since they aren't
land-contiguous with the mainland and Queen contiguity requires shared
borders).

**Global Moran's I** on the raw shrunken rate (`eb_posterior_rate_per_100k`),
Queen contiguity, 9,999-permutation inference: **I = 0.360** (expected under
complete spatial randomness ≈ 0), analytic z = 34.1, p ≈ 2.7×10⁻²⁵⁴
(analytic) / p = 0.0001 (permutation floor). This rejects spatial
randomness unambiguously — county-level brewery density is positively and
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

**Result: 217 hot spots, 5 cold spots.** The hot spots are not scattered —
their connected-component structure (via the same Queen contiguity graph)
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
Chittenden (VT); Knox (ME); Leelanau (MI). The 5 cold spots are dense urban
cores with low per-capita counts — Bergen (NJ), New York/Manhattan (NY),
Hudson (NJ), Fulton (GA), Bronx (NY) — the expected mirror image of a
per-capita-rate hot spot analysis in dense metros.

**Robustness to the capture-rate correction**: re-running on
`eb_posterior_rate_per_100k_corrected` gives Moran's I = 0.299 (still highly
significant) and 186 FDR-significant hot spots (0 cold spots), with 96.2%
label agreement against the raw-rate run and 145 of 217 raw hot spots
confirmed hot under the correction. The clustering finding is not an
artifact of OBDB's uneven state-level capture rate.

**Implication for the two existing models**: this is grounds for a
follow-up, not a retraction — a spatially-aware model (e.g. a conditional
autoregressive prior instead of the flat national-mean or purely
covariate-based priors currently used) would likely produce tighter,
better-calibrated estimates for counties inside one of these five clusters
than the current models' independence assumption allows, since a county's
neighbors carry real information about it that neither Model A nor Model B
currently uses.

### 12.1 A spatially-aware alternative to Model A (`fit_spatial_car_model.py`)

The follow-up flagged above was built and validated. **Model**: a Bayesian
Negative-Binomial ICAR (intrinsic conditional autoregressive) model — county
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
to 1.039 — still below a reasonable 1.05 tolerance but not the stricter
1.01 bar the production fit meets — so the holdout fit's draws were
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
clusters** — this is the check that most directly answers "is this real
signal or just noise with a spatial label on it": among the 88 counties
that are both population-floored (≥50k adults 21+) and inside a confirmed
Section-12 cluster, hot-spot counties move UP in the spatial ranking
relative to Model A by a mean of +52.6 ranks (median +14.5; 75% moved up),
cold-spot counties move DOWN by a mean of −68.4 ranks, and the remaining
not-significant counties drift only slightly (mean −6.1) — exactly the
pattern a real spatial effect should produce, derived from a completely
separate analysis (Getis-Ord Gi* clustering) than the one that fit this
model. Overall rank correlation with Model A stays high (Spearman ρ=0.86,
n=799 population-floored CONUS counties) — this is a targeted correction
concentrated where the spatial signal says it should apply, not a
wholesale reshuffle. Individual examples: Wayne County, NY (a hot-spot
county) moves from rank 712 to 140 (+572); Troup County, GA (not
significant in the Gi* test but adjacent to genuinely low-density
neighbors) drops from 239 to 665 (−426).

Outputs: `data/processed/us_county_car_shrunken_rankings.parquet` (3,222
counties), `data/processed/us_county_raw_vs_car_rankings.csv` (799
population-floored CONUS counties, with `spot_type` joined in from the
Section 12 hot-spot analysis for exactly this comparison). **Not adopted as
the project's default ranking** — Model A remains the headline shrinkage
estimate — but this is now the strongest evidence in the project that doing
so would be a real accuracy improvement, not a stylistic preference: it
needs no new data, only a different (and validated) prior.

## 13. Symmetric and complementary views: brewery deserts and the state-level rollup

### 13.1 Brewery deserts (`scripts/build_brewery_deserts.py`)

Every ranking artifact in this project surfaces counties with unexpectedly
*high* density. The inverse — large-population counties with unexpectedly
*low* density, i.e. candidate areas of unmet market potential — is a
trivial re-sort of data already on hand
(`data/processed/us_county_brewery_deserts.csv`, 817 counties, population
≥50,000 floor applied for the same small-county-noise reason as every other
ranking here) but had never been produced as its own artifact.

Two views are computed: (1) the pure bottom of the corrected-shrunken
ranking among population-floored counties, and (2) a population-weighted
cross-cut of the 100 largest counties nationally by `adults_21plus`,
re-sorted by lowest corrected density — since a bottom-ranked 50k-population
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
outside Orlando) rather than remote or purely rural counties — breweries
appear to cluster into urban cores and gentrifying neighborhoods and skip
nearby large-population suburbs even where the underlying metro clearly
supports the category. Demographic differences between desert and
non-desert counties are real but small (median age 37.7 vs. 39.7; tourism
establishments 1.63 vs. 2.33 per 10k; median household income ~$78.1k vs.
~$80.0k) — no single covariate dominates.

Raw-shrunken and corrected-shrunken desert lists agree closely (Spearman
ρ=0.976, 17 of the bottom 20 shared) — unlike the high-density ranking,
where the capture-rate correction substantially reshuffles who's on top
(Section on Texas clipping, README Key Findings), it barely reshuffles who's
at the bottom. This makes sense mechanically: the correction scales a
county's estimate by `1/capture_rate`, which has the largest absolute effect
on counties that already have a meaningful brewery count to scale — a
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
calibrated), CO (9.31, calibrated), OR (8.82, calibrated), NM (7.46) —
dominated by small-population pooled-extrapolation states, a direct
consequence of the per-100k-adults denominator; WY entering the calibrated
top 3 this round (up from being pooled-estimated before) is itself a
consequence of its capture rate clipping to 1.0 (see Section 5.1).

**Biggest absolute raw-vs-corrected brewery-count gaps** (corrected −
OBDB): CA (+509, 763→1,272), PA (+304), VA (+236), NY (+166), NC (+162),
OH (+124), WI (+115), MN (+85), GA (+81), FL (+80) — dominated by large
states with low-to-mid capture rates, since the gap scales with both county
count and `1/capture_rate − 1`.

**A structural finding worth flagging methodologically**: the 23
directly-calibrated states show far more extreme and more variable
`rank_change` (state-mean SD = 60.4, range [−109, +80]) than the 28
pooled-estimate states (SD = 14.2, range [−15, +48]). This tracks
mechanically with capture-rate spread — calibrated states range from 0.465
(VA) to a clipped 1.0 (TX, IL, WV, WY, MO), so low-capture-rate calibrated
states get pushed sharply up in rank (PA +80.0 mean, VA +75.4, CT +45.8)
while clipped-to-1.0 states get pushed sharply down (IL −109.2, WV −108.2,
MO −107.8). The pooled regression, by construction, can only produce
capture rates in a narrow band (~0.50–0.80 around a 0.610 baseline,
modestly density-adjusted) — it structurally cannot express the extremes
real calibration data produces. Notably, this gap *widened* as more states
were calibrated this round rather than narrowing (the calibrated/pooled SD
ratio went from ~3.2x at 13 states to ~4.3x at 23) — more real measurements
kept revealing more extremes the pooled model can't reach, not fewer.
**This means the correction's effect on the 28 uncalibrated states is
systematically muted relative to what direct state-specific measurement
would likely show** — not because those states truly need smaller
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
unfiltered range — the raw panel has extreme outliers (national max
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
new module places labels via real text-bounding-box collision detection —
`Text.get_window_extent(renderer)` after `fig.canvas.draw()` — trying up to
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
Section on the Virginia join-key bug) — labeling off `NAME` would risk
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
tab was added alongside the existing map view — the same county/CBSA data,
filterable by name/state and sortable by any column via a click, with a row
click jumping back to the map with that unit selected — for finding a
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
comparison proved unreliable — local headless-Chrome testing in this
environment turned out to have its own unrelated ~500px viewport-size floor
that ignored the requested `--window-size`, which could easily have masked
or been mistaken for the real bug if diagnosis had stopped at "does the
screenshot look right."
