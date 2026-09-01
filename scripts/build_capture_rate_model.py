"""Fit the OBDB coverage-correction model on the calibration states.

Pools county-level (obdb_count, licensee_count, population density) data across
every state with a validated licensee registry, fits a fixed-effects and a
random-intercept (state) log-capture-ratio model, and writes the pooled dataset
used by src/breweries/capture_rate_model.py.

Run this after all build_{state}_county_dataset.py scripts have produced their
outputs.

Known caveats — several states' licensee registries measure a different
population than "OBDB-listed craft breweries," which shows up as a raw
capture rate at or above 100% (kept in the pooled model rather than excluded
without a principled reason — cherry-picking would be worse — but flagged
here and in the methods memo):
- MO: MO ATC's "Microbrewery" license category structurally excludes the
  state's large/regional breweries (Anheuser-Busch, Boulevard Brewing hold
  no license in this category), leaving several counties with OBDB-observed
  breweries but zero matching licensees. Raw ratio 166.2%, the highest of
  any calibration state.
- WY: brewers only need a wholesaler license if they self-distribute
  (W.S. 12-4-201); a brewery using a third-party distributor never appears
  on this source. Raw ratio 128.6%.
- TX: TABC's public license table is documented by TABC itself to exclude
  brewpub subordinate authorizations, so the reference undercounts, not
  OBDB overcounting. Raw ratio 122.2%.
- CA: ABC's export counts *licenses*, and many operators hold multiple
  licenses per brand (satellite tasting rooms, alternating proprietorships)
  plus some large non-craft manufacturers (e.g. major wineries' Type-01 beer
  licenses). Raw ratio 135.3%.
- VA: ABC's export similarly counts licensed *premises*, and several brands
  hold multiple Virginia sites. Raw ratio 120.1%.
- IL: ILCC's export is cumulative (includes expired licenses filtered by
  expiration date, no explicit status column) and companion license classes
  (base "Brewer" + a production-tier overlay) can double-list one physical
  site despite dedup — raw ratio 108.6%.
- WV: 100.0% exactly, at the boundary rather than clearly over it — ABCA's
  list is a dated PDF snapshot (~13 months stale as of this fetch), so this
  isn't read as meaningfully different from the >100% states above.

States investigated and confirmed to have no bulk open-data source (an
interactive-only search tool, a login-gated portal, bot/WAF protection, or
no centralized state-level registry at all) and are NOT calibration states:
MS, OH, VT, MN, TN, AZ, SC (first round investigated), plus AL, AK, AR, DE,
HI, ID, IN, IA, KS, LA, ME, MD, MT, NV, NH, NM, ND, OK, RI, SD, UT (second,
broader round covering every remaining state). TN, AZ, and SC also have
OBDB/OSM/CBP-only datasets (`build_{state}_county_dataset.py`) used
elsewhere for face-validity checks, not for this capture-rate model. See
docs/methods_memo.md Section 8 for the specific reason each state was
excluded.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from breweries.sources import tiger

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
    "KY": "liquor_count",
    "FL": "liquor_count",
    "CT": "liquor_count",
    "MA": "liquor_count",
    "MO": "liquor_count",
    "NE": "liquor_count",
    "NJ": "liquor_count",
    "WV": "liquor_count",
    "WY": "liquor_count",
    "DC": "liquor_count",
}
STATE_FIPS = {
    "NC": "37", "MI": "26", "CO": "08", "OR": "41",
    "WA": "53", "TX": "48", "GA": "13", "WI": "55", "PA": "42",
    "IL": "17", "CA": "06", "NY": "36", "VA": "51",
    "KY": "21", "FL": "12", "CT": "09", "MA": "25", "MO": "29",
    "NE": "31", "NJ": "34", "WV": "54", "WY": "56", "DC": "11",
}


def load_pooled_counties() -> pd.DataFrame:
    frames = []
    for state, col in STATE_LICENSEE_COL.items():
        df = pd.read_parquet(f"data/processed/{state.lower()}_county_analysis.parquet")
        df = df.rename(columns={col: "licensee_count"})
        df["state"] = state
        frames.append(df[["county_name", "state", "obdb_count", "licensee_count",
                           "adults_21plus", "total_population"]])
    return pd.concat(frames, ignore_index=True)


def load_land_area() -> pd.DataFrame:
    """Return one land-area row per (state, county_name) join key.

    Virginia has independent cities whose bare TIGER NAME collides with a
    same-named county (Fairfax, Franklin, Richmond, Roanoke each have both a
    "X City" and "X County") — VA's own county_name field disambiguates with
    a "city"/"County" suffix (matching TIGER's NAMELSAD), unlike every other
    calibration state, which uses the bare county name. Emit both a bare-name
    row (for every other state) and a NAMELSAD row (so VA's suffixed names
    also match) rather than picking one convention and silently dropping the
    other state's rows.
    """
    counties = tiger.load_counties()[["STATEFP", "NAME", "NAMELSAD", "ALAND"]]
    fips_to_state = {v: k for k, v in STATE_FIPS.items()}
    counties = counties[counties["STATEFP"].isin(fips_to_state)].copy()
    counties["state"] = counties["STATEFP"].map(fips_to_state)
    counties["sqmi"] = counties["ALAND"] / 2_589_988

    bare = counties[["state", "NAME", "sqmi"]].rename(columns={"NAME": "county_name"})
    full = counties[["state", "NAMELSAD", "sqmi"]].rename(columns={"NAMELSAD": "county_name"})
    return pd.concat([bare, full], ignore_index=True).drop_duplicates(subset=["state", "county_name"])


def main() -> None:
    pooled = load_pooled_counties()
    land = load_land_area()
    df = pooled.merge(land, on=["state", "county_name"], how="left")

    df["density"] = df["total_population"] / df["sqmi"]
    df = df[df["density"] > 0].copy()
    df["log_density"] = np.log(df["density"])
    df["log_capture_ratio"] = np.log((df["obdb_count"] + 0.5) / (df["licensee_count"] + 0.5))

    model_df = df[df["licensee_count"] > 0].copy()
    print(f"Counties in model: {len(model_df)}\n")

    print("=" * 70)
    print("Fixed effects: log_capture_ratio ~ log_density + C(state)")
    print("=" * 70)
    fe = smf.ols("log_capture_ratio ~ log_density + C(state)", data=model_df).fit()
    print(fe.summary())

    print("\n" + "=" * 70)
    print("Pooled (no state term): log_capture_ratio ~ log_density")
    print("=" * 70)
    pooled_model = smf.ols("log_capture_ratio ~ log_density", data=model_df).fit()
    print(pooled_model.summary())

    # The raw exposure-weighted ratio (obdb_count.sum()/licensee_count.sum(), see
    # "Statewide pooled capture rates" below) and an *unweighted* per-county log-ratio
    # regression are two different quantities, not just two estimates of the same
    # one: the aggregate ratio is dominated by a handful of large, high-capture
    # counties (Buncombe, Mecklenburg, Wake, Denver), while the unweighted regression
    # describes a typical county. Using the aggregate ratio as the flat baseline
    # together with the unweighted regression's density slope — which is what an
    # earlier version of capture_rate_model.py did — silently mixed those two
    # targets. Since correction_factor() applies its fallback to arbitrary
    # counties nationally (most of which are small/medium, not large metros), the
    # per-county quantity is the right one to predict, weighted by licensee_count
    # for statistical efficiency (larger-exposure counties give a less noisy
    # estimate of their own local capture ratio) without changing what the
    # coefficients describe.
    print("\n" + "=" * 70)
    print("WLS (weights=licensee_count): log_capture_ratio ~ log_density")
    print("=" * 70)
    wls_model = smf.wls("log_capture_ratio ~ log_density", data=model_df,
                         weights=model_df["licensee_count"]).fit()
    print(wls_model.summary())
    mean_ld = model_df["log_density"].mean()
    wls_rate_at_mean = np.exp(wls_model.params["Intercept"] + wls_model.params["log_density"] * mean_ld)
    print(f"\nWLS-implied capture rate at mean log_density: {wls_rate_at_mean:.4f}")

    print("\n" + "=" * 70)
    print("Random intercept: log_capture_ratio ~ log_density + (1|state)")
    print("=" * 70)
    mm = smf.mixedlm("log_capture_ratio ~ log_density", model_df, groups=model_df["state"]).fit()
    print(mm.summary())
    print(f"\nBetween-state random-effect variance: {mm.cov_re.iloc[0, 0]:.4f}")

    print("\n" + "=" * 70)
    print("Statewide pooled capture rates")
    print("=" * 70)
    by_state = model_df.groupby("state").agg(obdb=("obdb_count", "sum"), licensee=("licensee_count", "sum"))
    by_state["capture_rate"] = by_state["obdb"] / by_state["licensee"]
    print(by_state)
    overall = model_df["obdb_count"].sum() / model_df["licensee_count"].sum()
    print(f"\nExposure-weighted aggregate capture rate across {len(STATE_LICENSEE_COL)} states: {overall:.1%}")
    print("(descriptive only — NOT used as capture_rate_model.POOLED_CAPTURE_RATE; see WLS section above)")
    print(f"Mean log_density across model counties: {mean_ld:.4f}")
    print(f"\ncapture_rate_model.py constants to use:")
    print(f"  POOLED_CAPTURE_RATE = {wls_rate_at_mean:.3f}  (WLS intercept prediction at mean log_density)")
    print(f"  LOG_DENSITY_COEF = {wls_model.params['log_density']:.3f}  (WLS slope)")

    df.to_parquet("data/processed/pooled_calibration_with_density.parquet", index=False)
    print("\nWrote data/processed/pooled_calibration_with_density.parquet")


if __name__ == "__main__":
    main()
