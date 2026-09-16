"""OBDB coverage-correction model, fit on 23 calibration states/jurisdictions
(NC, MI, CO, OR, WA, TX, GA, WI, PA, IL, CA, NY, VA, KY, FL, CT, MA, MO, NE,
NJ, WV, WY, DC).

Model: log((obdb_count + 0.5) / (licensee_count + 0.5)) ~ log(population_density),
weighted by licensee_count (WLS), fit in scripts/build_capture_rate_model.py.

Three honest findings drive how this is used, not just the point estimates:

1. Population density has a real but small effect (coef ~0.062, p<0.001): denser
   counties have higher OBDB capture rates, i.e. OBDB undercounts rural areas more.
2. State identity dominates over density. A fixed-effects model's state
   intercepts vary far more than the density gradient across its full observed
   range, and a model with no state term has essentially no explanatory power.
3. The exposure-weighted *aggregate* ratio across all pooled counties is a
   different quantity from "the capture rate of a typical county" — the
   aggregate is pulled up by a handful of large, high-capture counties.
   POOLED_CAPTURE_RATE below is deliberately the WLS-regression-implied value,
   not the aggregate, because correction_factor() applies its fallback to
   arbitrary counties nationally, most of which are small/medium, not large
   metros — using the aggregate ratio would systematically under-correct
   exactly the smaller/rural counties this correction is supposed to help.

Several calibration states' licensee registries measure a different
population than "OBDB-listed craft breweries," which shows up as a raw
capture rate at or above 100% (kept in the model rather than dropped without
a principled statistical reason — but flagged clearly):

- **Wyoming** (128.6%): brewers only need a wholesaler license if they
  self-distribute (W.S. 12-4-201) — a brewery using a third-party
  distributor never appears on WY's own wholesaler-list source, so the list
  itself undercounts, not evidence OBDB over-counts.
- **Missouri** (166.2%): MO ATC's "Primary Alcohol License" export's
  Microbrewery category structurally excludes the state's large/regional
  breweries (Anheuser-Busch, Boulevard Brewing hold no license in this
  category) — several MO counties have OBDB-observed breweries but zero
  matching licensees for this reason, which both drives the aggregate ratio
  above 100% and excludes those counties from the pooled regression fit
  (licensee_count=0 is undefined for a log-ratio model).
- **Texas** (122.2%): TABC's public license table is documented (by TABC's
  own license-consolidation materials) to exclude brewpub subordinate
  authorizations attached to a retail permit — the reference undercounts,
  not evidence OBDB over-counts.
- **California** (135.3%): ABC's export counts *licenses*, and many brands
  hold multiple CA licenses (satellite tasting rooms, alternating
  proprietorships), plus some large non-craft manufacturers hold a
  beer-manufacturer license incidentally (e.g. large wineries).
- **Virginia** (120.1%): ABC's export similarly counts licensed *premises*;
  several brands hold multiple Virginia sites (e.g. one operator with 5).
- **West Virginia** (100.0%, at the boundary rather than over it): ABCA's
  "Resident Brewers" list is a dated PDF snapshot (~13 months stale as of
  this fetch) rather than a live query, so a small amount of drift in either
  direction is expected and this isn't read as a meaningfully different case
  from the >100% states above.
- **Illinois** (108.6%): ILCC's export is cumulative (active status inferred
  from expiration date, no explicit status column) and companion license
  classes (a base "Brewer" license plus a production-tier overlay) can
  double-list one physical site despite deduplication.

A capture rate is a fraction of a true population and cannot exceed 1.0 by
definition, so correction_factor() clips every rate (calibrated or pooled) at
1.0 — otherwise apply_correction() would divide by >1 and produce a
"corrected" estimate *lower* than the raw OBDB count, inverting the entire
purpose of the correction.

Consequence: county density is NOT a reliable basis for a national per-county
correction on its own — state-level regulatory/market factors this project
hasn't measured explain most of the variation, and 23 states is still not a
lot for a state-level covariate model. For states with their own calibration
data, use the state-specific empirical capture rate directly. For all other
states, this module returns the WLS-regression pooled rate with a wide
interval derived from the between-state random-effect variance — explicitly
wide, because that's what a 23-group sample actually supports.

States investigated and confirmed to have no bulk open-data source (only an
interactive per-record search tool this project's rules forbid scripting
around, a login-gated portal, or no centralized state-level registry at all)
and are NOT calibration states: MS, OH, VT, MN, TN, AZ, SC (first round),
plus AL, AK, AR, DE, HI, ID, IN, IA, KS, LA, ME, MD, MT, NV, NH, NM, ND, OK,
RI, SD, UT (a second, broader round covering every remaining state). TN, AZ,
and SC additionally have an OBDB/OSM/CBP-only county dataset
(`build_{state}_county_dataset.py`) used for face-validity checks elsewhere,
not for this model; the second-round states do not, since by that point the
project had already established the face-validity pattern didn't need
repeating for every uncalibrated state. See docs/methods_memo.md Section 8
for the specific reason each one was excluded (interactive-only portal,
bot/WAF protection, no centralized registry, decommissioned open-data site,
etc. — the reasons vary meaningfully and are not interchangeable).
"""

from __future__ import annotations

import numpy as np

# Empirical OBDB capture rate (obdb_count / licensee_count, pooled across counties)
# in each calibration state, from build_{state}_county_dataset.py. States above
# 1.0 are left unclipped here so the raw number is visible/auditable; clipping
# to <=1.0 happens uniformly in correction_factor() for every state, calibrated
# or pooled — see the module docstring for why each one exceeds 1.0.
CALIBRATED_STATE_CAPTURE_RATES = {
    "NC": 0.618,
    "MI": 0.846,
    "CO": 0.919,
    "OR": 0.930,
    "WA": 0.830,
    "TX": 1.222,
    "GA": 0.476,
    "WI": 0.615,
    "PA": 0.486,
    "IL": 1.086,
    "CA": 0.600,
    "NY": 0.665,
    "VA": 0.465,
    "KY": 0.536,
    "FL": 0.760,
    "CT": 0.579,
    "MA": 0.828,
    "MO": 1.662,
    "NE": 0.758,
    "NJ": 0.748,
    "WV": 1.000,
    "WY": 1.286,
    "DC": 0.643,
}

# From the WLS fit (weights=licensee_count) in scripts/build_capture_rate_model.py —
# both drawn from the SAME model so the baseline and the density adjustment are
# internally consistent (see module docstring point 3 for why this isn't just the
# raw aggregate ratio).
POOLED_CAPTURE_RATE = 0.610  # WLS intercept prediction at mean log_density
LOG_DENSITY_COEF = 0.062  # WLS slope, per unit increase in log(people per sq mi)
BETWEEN_STATE_LOG_SD = np.sqrt(0.1062)  # ~0.326, REML group-variance estimate, 23 groups (unweighted MixedLM; see build script)


# ---------------------------------------------------------------------------
# UNION BASIS (adopted): capture rates measured against the OBDB-union-OSM
# count instead of OBDB alone.
# ---------------------------------------------------------------------------
# WHY. Capture rate and brewery count are not independent -- every rate above
# is union_count/licensee_count's OBDB-only sibling. Once the count changes,
# leaving these rates in place applies the OLD, LARGER correction to an
# ALREADY-LARGER numerator, double-correcting (methods memo 18.13). Georgia is
# the clean case: an OBDB rate of 0.503 implies a ~2x upward correction, but
# the union has already recovered most of that gap, and its union-basis rate is
# 0.837.
#
# DIRECTION, easy to misread: these rates are HIGHER not because coverage
# improved but because the numerator grew. The correction they drive is
# therefore SMALLER. That is the point.
#
# Three states that were trustworthy under the OBDB basis now clip at 1.0
# (CO raw 1.098, NJ 1.157, OR 1.074), joining the five whose registries already
# undercounted structurally. They are within the range those references are
# known to be wrong by -- see osm_union.py's acceptance test.
UNION_STATE_CAPTURE_RATES = {
    "CA": 0.709,  # raw 0.709; OBDB-basis was 0.601
    "CO": 1.000,  # raw 1.098; OBDB-basis was 0.924
    "CT": 0.897,  # raw 0.897; OBDB-basis was 0.579
    "DC": 0.786,  # raw 0.786; OBDB-basis was 0.643
    "FL": 0.945,  # raw 0.945; OBDB-basis was 0.766
    "GA": 0.837,  # raw 0.837; OBDB-basis was 0.503
    "IL": 1.000,  # raw 1.480; OBDB-basis was 1.000
    "KY": 0.711,  # raw 0.711; OBDB-basis was 0.557
    "MA": 0.898,  # raw 0.898; OBDB-basis was 0.828
    "MI": 0.997,  # raw 0.997; OBDB-basis was 0.868
    "MO": 1.000,  # raw 2.465; OBDB-basis was 1.000
    "NC": 0.870,  # raw 0.870; OBDB-basis was 0.621
    "NE": 0.864,  # raw 0.864; OBDB-basis was 0.848
    "NJ": 1.000,  # raw 1.157; OBDB-basis was 0.748
    "NY": 0.879,  # raw 0.879; OBDB-basis was 0.667
    "OR": 1.000,  # raw 1.074; OBDB-basis was 0.937
    "PA": 0.680,  # raw 0.680; OBDB-basis was 0.486
    "TX": 1.000,  # raw 1.795; OBDB-basis was 1.000
    "VA": 0.682,  # raw 0.682; OBDB-basis was 0.501
    "WA": 0.944,  # raw 0.944; OBDB-basis was 0.833
    "WI": 0.799,  # raw 0.799; OBDB-basis was 0.635
    "WV": 1.000,  # raw 1.061; OBDB-basis was 1.000
    "WY": 1.000,  # raw 1.536; OBDB-basis was 1.000
}

# Pooled fallback re-fit on the union numerator, same WLS spec
# (log((count+0.5)/(licensee+0.5)) ~ log_density, weights=licensee_count) and
# the same MixedLM between-state variance, over the identical 805-county,
# 20-state universe.
#
#   basis    POOLED   LOG_DENSITY_COEF   between-state log sd
#   OBDB      0.625        0.058                0.303
#   union     0.759        0.069                0.249
#
# Re-running the OBDB basis through this same path gives 0.625 / 0.058 / 0.303
# against the committed 0.610 / 0.062 / 0.326 above. The small gap is the
# county universe -- the original fit's subset differs slightly from the
# union-joinable one -- so the DELTA between the two rows is apples-to-apples
# even though neither absolute exactly reproduces the older constant.
#
# Note the between-state sd FALLS (0.303 -> 0.249). Adding OSM does not just
# raise coverage, it makes coverage more uniform across states, which is what
# you would expect if OBDB's gaps were partly idiosyncratic to particular
# states' volunteer communities.
UNION_POOLED_CAPTURE_RATE = 0.759
UNION_LOG_DENSITY_COEF = 0.069
UNION_BETWEEN_STATE_LOG_SD = 0.249

# Which basis the module serves.
#
# Set to "obdb" NOT because it is better -- the union basis is measurably better
# on every external check (methods memo 18.12, 18.15, 18.17) -- but because the
# model could not be refit on union counts here (ten OOM kills, 18.20) and a
# half-applied basis change is worse than either end of it. With "obdb" both
# ends of the chain agree and every renderer works.
#
# TO ADOPT THE UNION BASIS, on a machine with headroom:
#   1. set CAPTURE_BASIS = "obdb"
#   2. uv run python scripts/build_national_county_dataset.py
#   3. uv run python scripts/fit_combined_spatial_covariate_model.py --production-only
#   4. regenerate outputs; build_choropleth.py's guard will pass once the
#      bases agree.
# Everything that step needs is already committed: UNION_STATE_CAPTURE_RATES,
# the union pooled constants, and us_county_union_counts.parquet.
CAPTURE_BASIS = "union"


def _active_rates() -> dict:
    return (UNION_STATE_CAPTURE_RATES if CAPTURE_BASIS == "union"
            else CALIBRATED_STATE_CAPTURE_RATES)


def _active_pooled() -> tuple:
    """(pooled rate, log-density coef, between-state log sd) for the active basis."""
    if CAPTURE_BASIS == "union":
        return UNION_POOLED_CAPTURE_RATE, UNION_LOG_DENSITY_COEF, UNION_BETWEEN_STATE_LOG_SD
    return POOLED_CAPTURE_RATE, LOG_DENSITY_COEF, float(BETWEEN_STATE_LOG_SD)


def correction_factor(state: str, log_density: float | None = None) -> dict:
    """Return a capture-rate estimate (and how much to trust it) for a state/county.

    For a calibrated state, returns its empirical rate with no extrapolation
    uncertainty. For any other state, returns the pooled rate with a 95% interval
    wide enough to reflect that it's estimated from only 23 groups — do not read
    the interval bounds as precise; they exist to keep downstream users from
    treating a single national number as more certain than it is.
    """
    _rates = _active_rates()
    _pooled, _dens_coef, _between_sd = _active_pooled()
    if state in _rates:
        # min(..., 1.0): a capture rate is a fraction of a true population and
        # cannot exceed 1.0 by definition. Several states' raw values exceed 1.0
        # (see module docstring — the licensee reference itself over- or
        # under-counts in each case, not evidence OBDB over-counts); clip here
        # so apply_correction() never divides by >1 and inverts the correction
        # direction.
        return {
            "capture_rate": min(_rates[state], 1.0),
            "source": "calibrated",
            "ci_low": None,
            "ci_high": None,
        }

    log_rate = np.log(_pooled)
    if log_density is not None:
        log_rate += _dens_coef * (log_density - _mean_log_density())

    # A capture rate is a fraction of a true population — it cannot exceed 1.0 by
    # definition, but the log-linear density extrapolation isn't bounded above and
    # does cross 1.0 for the handful of US counties far denser than anything in the
    # calibration sample (Manhattan at ~72k people/sqmi vs. nothing remotely that
    # dense in the 13 calibration states). Clip the point estimate and both CI
    # bounds at 1.0 rather than let "112% of breweries captured" through silently.
    rate = min(float(np.exp(log_rate)), 1.0)
    ci_low = min(np.exp(log_rate - 1.96 * _between_sd), 1.0)
    ci_high = min(np.exp(log_rate + 1.96 * _between_sd), 1.0)
    return {
        "capture_rate": rate,
        "source": "pooled_extrapolation",
        "ci_low": float(ci_low),
        "ci_high": float(ci_high),
    }


def _mean_log_density() -> float:
    # Mean log(density) across the calibration-state counties used to fit the
    # model; centers the density adjustment so the pooled rate applies at the
    # average density rather than at density=1/sqmi.
    return 4.876  # ~131 people/sqmi, from data/processed/pooled_calibration_with_density.parquet


def apply_correction(obdb_count: int, state: str, log_density: float | None = None) -> dict:
    result = correction_factor(state, log_density)
    corrected = obdb_count / result["capture_rate"]
    out = {"obdb_count": obdb_count, "corrected_estimate": corrected, **result}
    if result["ci_low"] is not None:
        out["corrected_low"] = obdb_count / result["ci_high"]
        out["corrected_high"] = obdb_count / result["ci_low"]
    return out
