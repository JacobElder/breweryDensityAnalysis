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


def correction_factor(state: str, log_density: float | None = None) -> dict:
    """Return a capture-rate estimate (and how much to trust it) for a state/county.

    For a calibrated state, returns its empirical rate with no extrapolation
    uncertainty. For any other state, returns the pooled rate with a 95% interval
    wide enough to reflect that it's estimated from only 13 groups — do not read
    the interval bounds as precise; they exist to keep downstream users from
    treating a single national number as more certain than it is.
    """
    if state in CALIBRATED_STATE_CAPTURE_RATES:
        # min(..., 1.0): a capture rate is a fraction of a true population and
        # cannot exceed 1.0 by definition. Several states' raw values exceed 1.0
        # (see module docstring — the licensee reference itself over- or
        # under-counts in each case, not evidence OBDB over-counts); clip here
        # so apply_correction() never divides by >1 and inverts the correction
        # direction.
        return {
            "capture_rate": min(CALIBRATED_STATE_CAPTURE_RATES[state], 1.0),
            "source": "calibrated",
            "ci_low": None,
            "ci_high": None,
        }

    log_rate = np.log(POOLED_CAPTURE_RATE)
    if log_density is not None:
        log_rate += LOG_DENSITY_COEF * (log_density - _mean_log_density())

    # A capture rate is a fraction of a true population — it cannot exceed 1.0 by
    # definition, but the log-linear density extrapolation isn't bounded above and
    # does cross 1.0 for the handful of US counties far denser than anything in the
    # calibration sample (Manhattan at ~72k people/sqmi vs. nothing remotely that
    # dense in the 13 calibration states). Clip the point estimate and both CI
    # bounds at 1.0 rather than let "112% of breweries captured" through silently.
    rate = min(float(np.exp(log_rate)), 1.0)
    ci_low = min(np.exp(log_rate - 1.96 * BETWEEN_STATE_LOG_SD), 1.0)
    ci_high = min(np.exp(log_rate + 1.96 * BETWEEN_STATE_LOG_SD), 1.0)
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
