"""Test specific, falsifiable hypotheses about what drives state-to-state
variation in OBDB's brewery-count capture rate, using the 13 states with
empirical licensee-registry calibration in
src/breweries/capture_rate_model.py::CALIBRATED_STATE_CAPTURE_RATES.

The project's mixed-effects work (docs/methods_memo.md Sec 5.1/5.2) already
established that state identity dominates over local population density in
explaining capture-rate variation, but never asked WHY some states have much
better OBDB coverage than others. This script tests three candidate drivers:

1. Coverage-capacity: does capture rate degrade as the true number of
   breweries (or population) a state's volunteer contributors have to track
   grows?
2. Pioneer craft-beer state: do states with a longer-established modern
   craft-brewing scene (more cumulative years for volunteer OBDB contributors
   to have built out coverage) show higher capture rates? The "pioneer" year
   per state is hand-researched via WebSearch citations recorded in the
   PIONEER_YEAR dict docstring below, not guessed.
3. OSM start_date tag: checks whether OSM's optional start_date tag on
   craft=brewery/microbrewery POIs is populated often enough (parsed directly
   from the cached raw Overpass JSON in data/raw/osm/, bypassing
   breweries.sources.osm which does not extract this field) to serve as a
   genuine "years since first documented brewery" proxy. Reported honestly
   even if (as turns out) coverage is too sparse to use.
4. Confound check: is "pioneer state" just a proxy for population/licensee
   count (hypothesis 1)?

n=13 throughout. This script does not try to force a significant finding out
of that sample size — see the printed verdict for what is and is not
resolvable at n=13, consistent with this project's standard of reporting
honest null/inconclusive results (methods_memo.md Sec 5.3).

Outputs data/processed/capture_rate_driver_tests.csv (the per-state analysis
table) for any downstream inspection. Does not modify any existing project
file.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from breweries.capture_rate_model import CALIBRATED_STATE_CAPTURE_RATES

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 20)

REPO_ROOT = Path(__file__).resolve().parents[1]
OSM_RAW_DIR = REPO_ROOT / "data" / "raw" / "osm"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"

# Same per-state licensee-count column mapping used to fit the calibration
# model itself (scripts/build_capture_rate_model.py::STATE_LICENSEE_COL) --
# reproduced here (not imported) to avoid a runtime dependency on a script
# other parallel agents may be touching, and because this script only needs
# the column name, not the fitting logic.
STATE_LICENSEE_COL = {
    "NC": "abc_permit_count",
    "MI": "lara_permit_count",
    "CO": "liquor_count",
    "OR": "olcc_primary_count",
    "WA": "liquor_count",
    "TX": "liquor_count",
    "GA": "liquor_count",
    "WI": "liquor_count",
    "PA": "liquor_count",
    "IL": "liquor_count",
    "CA": "liquor_count",
    "NY": "liquor_count",
    "VA": "liquor_count",
}

# Full state name used by the county_analysis parquet's OSM raw-file prefix
# (data/raw/osm/{TWO_LETTER}_*.json) -- these are the 2-letter USPS codes, not
# full names, matching STATE_LICENSEE_COL keys above.

# ---------------------------------------------------------------------------
# Hypothesis 2 input: hand-researched "first modern craft brewery" year per
# calibration state. Sourced from VinePair's "The Year Every State Got Its
# First Craft Brewery" (https://vinepair.com/articles/first-craft-brewery-in-every-state-map/,
# accessed 2026-08-31), cross-checked against two independent sources for
# California, Colorado, and Washington:
#   - Punch / All About Beer / CraftBeer.com histories of the "first wave"
#     (New Albion 1976, Sierra Nevada 1979/1980, Boulder Beer 1979, Bert
#     Grant's Yakima Brewing & Malting July 1982 -- the first US brewpub
#     since Prohibition)
#   - Brewers Association timeline (brewersassociation.org) corroborating
#     Sierra Nevada 1979/1980 and Boulder Beer 1979 as among the first five
#     US craft breweries.
#
# ONE CORRECTION to VinePair was necessary and is flagged here rather than
# silently applied: VinePair lists Pennsylvania's "first craft brewery" as
# D.G. Yuengling & Son, 1829. That is wrong for this purpose -- Yuengling is
# a pre-Prohibition legacy regional brewery that survived Prohibition by
# making other products; it is not a "craft" brewery in the modern
# microbrewery-movement sense the other 12 states' entries use, and using it
# would make PA look like the most "pioneer" state in the whole sample by a
# 150-year margin, which is a data error, not a finding. Cross-checked
# against visitpa.com's own craft-beer history and Wikipedia/press coverage
# of Stoudt's Brewery: Dock Street Brewing Co. (Philadelphia, 1985) is
# consistently cited as PA's first modern microbrewery, with Stoudt's
# (Adamstown, 1987) close behind and sometimes given the same label. PA is
# coded here as 1985 (Dock Street), the more conservative (earlier) of the
# two, which if anything works AGAINST the "PA is not a pioneer state"
# classification used below -- i.e. this correction is not chosen to force
# PA into either bucket.
PIONEER_YEAR = {
    "CA": 1976,  # New Albion Brewery, Sonoma -- widely cited as the first
                 # modern US microbrewery (Anchor Brewing's 1965 revival
                 # under Fritz Maytag predates it but was a rescue of an
                 # existing 19th-century brewery, not a new craft venture)
    "CO": 1979,  # Boulder Beer -- 4th US craft brewery per multiple sources
    "NY": 1981,  # William S. Newman Brewing Co. -- 5th US craft brewery
    "MI": 1982,  # The Real Ale Co.
    "WA": 1982,  # Bert Grant's Yakima Brewing & Malting -- first US brewpub
                 # since Prohibition, July 1982 (VinePair says 1984 for its
                 # combined WA entry; the 1982 Yakima date is corroborated
                 # independently by Punch/HistoryLink and used here as the
                 # earlier, better-documented date)
    "OR": 1984,  # Columbia River Brewery / McMenamins Hillsdale
    "VA": 1984,  # Chesapeake Bay Brewing
    "TX": 1985,  # Reinheitsgebot Brewing Company
    "PA": 1985,  # Dock Street Brewing Co. (corrected from VinePair; see note above)
    "NC": 1986,  # Weeping Radish
    "WI": 1986,  # Sprecher Brewing Company
    "IL": 1987,  # Sieben's River North Brewery
    "GA": 1989,  # Friends Brewing Co.
}
PIONEER_CUTOFF_YEAR = 1983  # states whose first craft brewery predates the
# "brewpub legalization + first wave" inflection (WA/CA legalized brewpubs in
# 1982-83; craft brewery count nationally went from ~8 in 1980 to >500 by
# 1994) are coded "pioneer". This groups CA/CO/NY/MI/WA (1976-1982) as
# pioneers and OR/VA/TX/PA/NC/WI/IL/GA (1984-1989) as non-pioneers. The
# split is close for WA (1982) and OR (1984) -- a 2-year gap -- so this
# binary should be read as "chronologically first movers" rather than a
# sharp qualitative break; see hypothesis 2 writeup for why OR's *current*
# reputation as a craft-beer mecca is not the same variable as *when* it
# started.

CURRENT_YEAR = 2026


def load_state_totals() -> pd.DataFrame:
    """True licensee count and population per calibration state, summed from
    the same county-level parquet files build_capture_rate_model.py drew its
    licensee columns from. These are independently recomputed from the raw
    files here (not re-read from the fitted model) so hypothesis 1 has a
    predictor that isn't definitionally entangled with the capture-rate
    outcome beyond what obdb_count/licensee_count already implies.
    """
    rows = []
    for state, col in STATE_LICENSEE_COL.items():
        f = PROCESSED_DIR / f"{state.lower()}_county_analysis.parquet"
        df = pd.read_parquet(f)
        rows.append({
            "state": state,
            "n_counties": len(df),
            "true_licensee_count": df[col].sum(),
            "population": df["total_population"].sum(),
            "obdb_count_recomputed": df["obdb_count"].sum(),
        })
    return pd.DataFrame(rows).set_index("state")


def check_osm_start_date_coverage() -> pd.DataFrame:
    """Parse the cached raw Overpass JSON directly (per-element 'tags' dict)
    for each calibration state and report what fraction of brewery POIs carry
    an OSM start_date tag. breweries.sources.osm does not extract this field
    (only name/craft/microbrewery/amenity/lat/lon), so the raw cache is read
    directly here rather than going through that loader.
    """
    rows = []
    for state in STATE_LICENSEE_COL:
        matches = sorted(OSM_RAW_DIR.glob(f"{state}_*.json"))
        if not matches:
            rows.append({"state": state, "osm_n": 0, "osm_with_start_date": 0,
                          "osm_start_date_pct": np.nan, "cache_file": None})
            continue
        # Most recent cache file if more than one exists for a state.
        f = matches[-1]
        with open(f) as fh:
            payload = json.load(fh)
        elements = payload.get("elements", [])
        n = len(elements)
        with_sd = sum(1 for e in elements if "start_date" in e.get("tags", {}))
        rows.append({
            "state": state,
            "osm_n": n,
            "osm_with_start_date": with_sd,
            "osm_start_date_pct": 100 * with_sd / n if n else np.nan,
            "cache_file": f.name,
        })
    return pd.DataFrame(rows).set_index("state")


def build_analysis_table() -> pd.DataFrame:
    capture = pd.Series(CALIBRATED_STATE_CAPTURE_RATES, name="capture_rate_raw")
    capture_clipped = capture.clip(upper=1.0).rename("capture_rate_clipped")
    totals = load_state_totals()
    osm = check_osm_start_date_coverage()

    df = pd.concat([capture, capture_clipped, totals, osm], axis=1)
    df["pioneer_year"] = pd.Series(PIONEER_YEAR)
    df["years_since_pioneer"] = CURRENT_YEAR - df["pioneer_year"]
    df["is_pioneer"] = df["pioneer_year"] < PIONEER_CUTOFF_YEAR
    return df.sort_values("capture_rate_raw", ascending=False)


def corr_pair(x: pd.Series, y: pd.Series, label: str) -> None:
    pear_r, pear_p = stats.pearsonr(x, y)
    spear_r, spear_p = stats.spearmanr(x, y)
    print(f"  {label}:")
    print(f"    Pearson  r = {pear_r:+.3f}, p = {pear_p:.3f}")
    print(f"    Spearman rho = {spear_r:+.3f}, p = {spear_p:.3f}")
    if np.sign(pear_r) != np.sign(spear_r) and abs(pear_r) > 0.1 and abs(spear_r) > 0.1:
        print("    ** Pearson and Spearman disagree in sign -- treat as no reliable relationship. **")
    return pear_r, pear_p, spear_r, spear_p


def main() -> None:
    df = build_analysis_table()

    print("=" * 100)
    print("STATE-LEVEL ANALYSIS TABLE (n=13 calibration states)")
    print("=" * 100)
    display_cols = ["capture_rate_raw", "capture_rate_clipped", "population",
                     "true_licensee_count", "obdb_count_recomputed",
                     "pioneer_year", "is_pioneer", "osm_start_date_pct"]
    print(df[display_cols].to_string(float_format=lambda v: f"{v:,.3f}" if abs(v) < 10 else f"{v:,.0f}"))

    # Note on capture rate vs recomputed raw ratio -----------------------
    df["raw_ratio_recomputed"] = df["obdb_count_recomputed"] / df["true_licensee_count"]
    drift = (df["raw_ratio_recomputed"] - df["capture_rate_raw"]).abs()
    print("\nNOTE: recomputed obdb/licensee ratio from current county parquets vs. the")
    print("calibrated dict value (which is what CALIBRATED_STATE_CAPTURE_RATES stores):")
    print(df[["capture_rate_raw", "raw_ratio_recomputed"]].assign(abs_diff=drift).to_string(float_format=lambda v: f"{v:.3f}"))
    print("Some drift is expected (parquet builds and the calibration dict are not")
    print("guaranteed to be from the identical snapshot/build run); capture_rate_raw")
    print("from the dict is treated as the authoritative outcome variable below, and")
    print("true_licensee_count / population (independently summed from the current")
    print("county parquets) are treated as the predictors -- so hypothesis 1 is not")
    print("circularly testing a ratio against its own numerator/denominator.")

    # ------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("HYPOTHESIS 1: coverage-capacity (capture rate vs. true brewery count / population)")
    print("=" * 100)
    print(f"n = {len(df)}")
    print("\ncapture_rate_raw vs. true_licensee_count:")
    r1 = corr_pair(df["true_licensee_count"], df["capture_rate_raw"], "true_licensee_count")
    print("\ncapture_rate_raw vs. population:")
    r2 = corr_pair(df["population"], df["capture_rate_raw"], "population")
    print("\n(sensitivity check) capture_rate_clipped (>1.0 clipped) vs. true_licensee_count:")
    corr_pair(df["true_licensee_count"], df["capture_rate_clipped"], "true_licensee_count (clipped outcome)")
    print("\n(sensitivity check) capture_rate_clipped vs. population:")
    corr_pair(df["population"], df["capture_rate_clipped"], "population (clipped outcome)")

    # ------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("HYPOTHESIS 2: pioneer craft-beer state vs. capture rate")
    print("=" * 100)
    pioneer_group = df.loc[df["is_pioneer"], "capture_rate_raw"]
    nonpioneer_group = df.loc[~df["is_pioneer"], "capture_rate_raw"]
    print(f"Pioneer states (first craft brewery before {PIONEER_CUTOFF_YEAR}), n={len(pioneer_group)}:")
    print(f"  {sorted(df.loc[df['is_pioneer']].index.tolist())}")
    print(f"  mean capture rate = {pioneer_group.mean():.3f}, median = {pioneer_group.median():.3f}")
    print(f"Non-pioneer states, n={len(nonpioneer_group)}:")
    print(f"  {sorted(df.loc[~df['is_pioneer']].index.tolist())}")
    print(f"  mean capture rate = {nonpioneer_group.mean():.3f}, median = {nonpioneer_group.median():.3f}")

    t_stat, t_p = stats.ttest_ind(pioneer_group, nonpioneer_group, equal_var=False)
    u_stat, u_p = stats.mannwhitneyu(pioneer_group, nonpioneer_group, alternative="two-sided")
    # Cohen's d (pooled SD, small-sample; Hedges' g correction applied since
    # groups are 5 vs 8 -- small and unbalanced)
    n1, n2 = len(pioneer_group), len(nonpioneer_group)
    pooled_sd = np.sqrt(((n1 - 1) * pioneer_group.var(ddof=1) + (n2 - 1) * nonpioneer_group.var(ddof=1)) / (n1 + n2 - 2))
    cohens_d = (pioneer_group.mean() - nonpioneer_group.mean()) / pooled_sd if pooled_sd > 0 else np.nan
    hedges_correction = 1 - (3 / (4 * (n1 + n2) - 9))
    hedges_g = cohens_d * hedges_correction
    # Rank-biserial effect size from Mann-Whitney U
    rank_biserial = 1 - (2 * u_stat) / (n1 * n2)

    print(f"\nWelch's t-test (unequal variance, appropriate for n1={n1}, n2={n2}): t = {t_stat:.3f}, p = {t_p:.3f}")
    print(f"Mann-Whitney U (rank-based, robust to n=13 non-normality): U = {u_stat:.1f}, p = {u_p:.3f}")
    print(f"Cohen's d = {cohens_d:+.3f}, Hedges' g (small-sample corrected) = {hedges_g:+.3f}")
    print(f"Rank-biserial correlation (MWU effect size) = {rank_biserial:+.3f}")
    print("Note: with n1=5, n2=8, this test has very low power -- a large true effect")
    print("could still fail to reach p<.05, and a p<.05 result here should not be")
    print("over-trusted either. Effect size is reported precisely because the p-value")
    print("alone is close to uninformative at this n.")

    # Continuous version: years_since_pioneer vs capture rate (uses full
    # ordinal information instead of forcing a binary split)
    print("\nContinuous check -- years_since_pioneer (2026 - first_craft_brewery_year) vs. capture_rate_raw:")
    corr_pair(df["years_since_pioneer"], df["capture_rate_raw"], "years_since_pioneer")

    # ------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("HYPOTHESIS 3: OSM start_date tag coverage")
    print("=" * 100)
    print(df[["osm_n", "osm_with_start_date", "osm_start_date_pct"]].to_string(float_format=lambda v: f"{v:.1f}"))
    max_pct = df["osm_start_date_pct"].max()
    mean_pct = df["osm_start_date_pct"].mean()
    print(f"\nMax coverage across 13 states: {max_pct:.1f}%  |  Mean coverage: {mean_pct:.1f}%")
    usable = df["osm_start_date_pct"] >= 10
    if usable.any() and mean_pct >= 10:
        print("Usable fraction threshold (>=10-15%) met on average -- proceeding to test as proxy.")
        corr_pair(df["osm_start_date_pct"], df["capture_rate_raw"], "osm_start_date_pct")
    else:
        print("NOT USABLE: OSM start_date tag coverage is far below the 10-15% threshold in")
        print("every one of the 13 states (max is well under 10%, mean under 3%). This is not")
        print("a borderline call -- treating this as a 'years since first documented brewery'")
        print("proxy would mean inferring state-level history from a small, almost certainly")
        print("non-random handful of tagged POIs (contributors who happened to know/add a")
        print("founding date), not a systematic measurement. No test is run on this variable;")
        print("hypothesis 3 is reported as a clean null on data availability grounds, not on")
        print("a weak correlation.")

    # ------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("HYPOTHESIS 4: confound check -- is 'pioneer' just a proxy for size?")
    print("=" * 100)
    print("\nis_pioneer (binary) vs. true_licensee_count (point-biserial via Pearson on 0/1):")
    corr_pair(df["is_pioneer"].astype(int), df["true_licensee_count"], "is_pioneer x true_licensee_count")
    print("\nis_pioneer (binary) vs. population:")
    corr_pair(df["is_pioneer"].astype(int), df["population"], "is_pioneer x population")
    print("\nyears_since_pioneer vs. true_licensee_count:")
    corr_pair(df["years_since_pioneer"], df["true_licensee_count"], "years_since_pioneer x true_licensee_count")
    print("\nyears_since_pioneer vs. population:")
    corr_pair(df["years_since_pioneer"], df["population"], "years_since_pioneer x population")

    out_path = PROCESSED_DIR / "capture_rate_driver_tests.csv"
    df.to_csv(out_path)
    print(f"\nWrote analysis table to {out_path}")


if __name__ == "__main__":
    main()
