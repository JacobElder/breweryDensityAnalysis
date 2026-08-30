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

Rendered outputs (`scripts/build_choropleth.py`, `scripts/build_top50_table.py`,
all reading Model A's output): a national county-level choropleth of the
shrunken rate, a population-floored variant of the same map (counties under
50k adults 21+ shown gray rather than colored, since shrinkage reduces but
does not eliminate small-county noise), and a top-50-counties table image.

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

### 5.1 Four-state calibration

State licensee/permit registries were obtained for NC (ABC Commission), MI
(LARA Master License List), CO (Socrata open-data liquor licenses), and OR
(OLCC Socrata liquor licenses) — see `src/breweries/sources/{nc_abc,mi_lara,
co_liquor,or_olcc}.py`. All four track the Brewers Association's own 2025 state
totals within 1-4%, which is why they're trusted as the calibration anchor
rather than OBDB or CBP:

| State | Licensee count | BA 2025 total | Licensee/BA |
|---|---|---|---|
| NC | 422 | 418 | 101% |
| MI | 395 | 410 | 96% |
| CO | 408 | 423 | 97% |
| OR | 285 (primary) | 297 | 96% |

Against that anchor, OBDB's capture rate varies enormously by state:

| State | OBDB capture rate |
|---|---|
| NC | 62% |
| MI | 85% |
| CO | 92% |
| OR | 93% |
| **Pooled (4 states)** | **82%** |

CBP (NAICS 312120) is worse everywhere (27-54% capture) — consistent with its
known brewpub-misclassification problem (brewpubs often file under NAICS
722511, restaurants, not 312120).

### 5.2 Why there is no reliable national correction factor

A mixed-effects model (`scripts/build_capture_rate_model.py`) regressing
log(OBDB/licensee ratio) on log(population density), with state as a random
intercept, found:

- Density has a real, statistically significant effect (coefficient ≈0.07-0.08,
  p<0.01): denser counties have higher OBDB capture rates, i.e. **OBDB
  undercounts rural areas more**, as hypothesized.
- But **state identity dominates**: the four state intercepts range over roughly
  4x the magnitude of the density effect across its full observed range,
  and a pooled model with no state term has essentially no explanatory power
  (R²=0.008).

With only 4 calibration states, there is not enough information to separate
"what predicts capture rate" from "which state this happens to be." The
practical consequence, implemented in `src/breweries/capture_rate_model.py`:
counties in a calibrated state use that state's empirical rate; every other
county uses the pooled 82% rate with a **deliberately wide** interval (derived
from the between-state variance, not tightened by density) — roughly 55-140%
of the point estimate at the 95% level. That width is the honest answer, not a
bug: four states cannot support a tighter one.

### 5.3 What OSM adds, and what a naive combination gets wrong

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
- **No Brewers Association bulk download or directory scrape.** Four single
  state-total lookups were made (NC, MI, CO, OR), each dated and cited inline
  in the relevant build script, per the project's explicit "no bulk
  download/scrape" constraint.

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

## 10. What these numbers can't support

- County-level rankings below the population floor, or for counties with fewer
  than a handful of licensee-registry data points, should not be read as
  precise — they're shrunk toward priors for exactly this reason, but shrinkage
  reduces noise, it doesn't manufacture missing ground truth.
- Any state without its own calibration data is carrying OBDB's raw undercount
  (7-38% observed range across the 4 calibration states) partially corrected by a wide, honestly-uncertain
  interval — not a precise correction.
- The choropleth and rankings are **not** capture-rate-corrected by default
  (the map explicitly says so); `capture_rate_model.apply_correction()` exists
  to produce a corrected version but doing so at every county nationally
  compounds the state-vs-density confound described in Section 5.2.
- OSM data has been fetched for all 50 states + DC (`data/raw/osm/`), but it
  is not incorporated into the headline county/CBSA/place datasets or either
  model — the only place it's used quantitatively is the NC capture-recapture
  diagnostic in Section 5.3, and per that section's finding, using it as a
  second signal at national scale would need the same correlated-crowdsourcing
  caveat, not a straightforward "more data is better" treatment.
