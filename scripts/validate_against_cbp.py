"""Validate county brewery counts against County Business Patterns (NAICS
312120) -- the one independent, NATIONAL reference this project has.

WHY THIS IS WORTH DOING
-----------------------
Ground truth so far comes from 23 state licensee registries, of which only 18
are trustworthy enough to score against (five undercount structurally). That
caps external validation at 18 states. CBP covers the whole country from a
single administrative source, it is already fetched
(`data/raw/cbp/US_county_312120_*.csv`), and until now it has been used for
exactly two anecdotes in the methods memo rather than systematically.

The decisive property is that CBP's bias is ORTHOGONAL to OBDB's. OBDB and OSM
are both crowdsourced, so they miss the same kinds of brewery (new, small,
low online profile) -- which is why capture-recapture between them failed
(memo Section 5.3, and the three-source attempt overestimated Colorado by 10x).
CBP's failure mode is completely different: NAICS misclassification, where a
brewpub is filed under 722511 (full-service restaurants) instead of 312120.
Two sources failing for unrelated reasons make agreement meaningful evidence
and disagreement a targeted signal, in a way two crowdsourced sources never can.

WHAT CBP CANNOT DO
------------------
- **Suppression is not random.** Small cells are withheld for disclosure
  avoidance, so CBP is systematically missing in exactly the small/rural
  counties where the model's own uncertainty is largest. Suppressed values are
  NaN here and are never read as zero. That makes CBP useful for validating
  the TOP of the distribution -- which is where the documented urban bias
  lives (memo 18.3) -- and silent about the 59%-zero tail.
- **Brewpub misclassification is state-structured**, because states differ in
  brewpub share. A state-level CBP/OBDB gap is therefore not automatically an
  OBDB error, and is not treated as one below.

So this is a CORROBORATION exercise, not a second ground truth. It answers
"do two independently-biased sources agree about which counties have breweries,
and where do they disagree enough to be worth a look?"

Writes data/processed/us_county_cbp_validation.csv.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from breweries.sources import cbp

ANALYSIS_PATH = "data/processed/us_county_analysis.parquet"
UNION_PATH = "data/processed/us_county_union_counts.parquet"
RANKINGS_PATH = "data/processed/us_county_combined_model_rankings.parquet"
OUT_PATH = "data/processed/us_county_cbp_validation.csv"

# Flag a county for hygiene review when the two sources differ by more than
# this factor in either direction. Deliberately loose: brewpub
# misclassification alone can produce a 2x gap with no error on either side.
DIVERGENCE_FACTOR = 3.0


def main() -> None:
    cbp_df = cbp.load_county_national()
    estab_col = next(c for c in cbp_df.columns if c.upper() == "ESTAB")
    cbp_df = cbp_df.rename(columns={estab_col: "cbp_estab"})
    cbp_df["county_geoid"] = (cbp_df["state"].astype(str).str.zfill(2)
                              + cbp_df["county"].astype(str).str.zfill(3))
    cbp_df["cbp_estab"] = pd.to_numeric(cbp_df["cbp_estab"], errors="coerce")
    cbp_df = cbp_df.dropna(subset=["cbp_estab"])[["county_geoid", "cbp_estab"]]
    print(f"CBP counties with a non-suppressed NAICS 312120 count: {len(cbp_df):,}")

    df = pd.read_parquet(ANALYSIS_PATH)[
        ["county_geoid", "county_name", "state_abbr", "obdb_count", "adults_21plus"]]
    union = pd.read_parquet(UNION_PATH)[["county_geoid", "union_count"]]
    rank = pd.read_parquet(RANKINGS_PATH)[
        ["county_geoid", "combined_posterior_rate_per_100k"]]

    d = (df.merge(union, on="county_geoid", how="left")
           .merge(rank, on="county_geoid", how="left")
           .merge(cbp_df, on="county_geoid", how="inner"))
    d["union_count"] = d["union_count"].fillna(d["obdb_count"])
    d["model_count"] = d["combined_posterior_rate_per_100k"] / 1e5 * d["adults_21plus"]
    print(f"Matched to the analysis universe: {len(d):,} counties "
          f"({d['adults_21plus'].sum() / df['adults_21plus'].sum():.0%} of adults 21+)\n")

    # --- agreement --------------------------------------------------------
    print("=" * 70)
    print("AGREEMENT WITH CBP (Spearman rank correlation, and median ratio)")
    print("=" * 70)
    rows = []
    for col, label in [("obdb_count", "OBDB only"),
                       ("union_count", "OBDB union OSM"),
                       ("model_count", "model-implied count")]:
        rho, p = stats.spearmanr(d[col], d["cbp_estab"])
        ratio = d[col] / d["cbp_estab"].replace(0, np.nan)
        rows.append({"source": label, "spearman_rho": round(float(rho), 4),
                     "median_ratio_vs_cbp": round(float(np.nanmedian(ratio)), 3),
                     "median_abs_log_gap": round(
                         float(np.nanmedian(np.abs(np.log(ratio.replace(0, np.nan))))), 3)})
    agree = pd.DataFrame(rows)
    print(agree.to_string(index=False))
    print("\n  A ratio above 1.0 is expected: CBP misses brewpubs filed under 722511,")
    print("  so OBDB legitimately lists more breweries than CBP does in most counties.")
    print("  The informative column is the RANK correlation -- whether the two")
    print("  sources order counties the same way despite unrelated biases.")

    # --- where they disagree ---------------------------------------------
    d["ratio_union_cbp"] = d["union_count"] / d["cbp_estab"].replace(0, np.nan)
    diverge = d[(d["ratio_union_cbp"] > DIVERGENCE_FACTOR)
                | (d["ratio_union_cbp"] < 1 / DIVERGENCE_FACTOR)].copy()
    print("\n" + "=" * 70)
    print(f"DIVERGENCE >{DIVERGENCE_FACTOR:.0f}x -- candidates for hygiene review")
    print("=" * 70)
    print(f"  {len(diverge)} of {len(d)} matched counties ({len(diverge) / len(d):.1%})")

    # Split by DIRECTION before taking extremes: nsmallest over the combined
    # set silently pulls in over-listing counties whenever fewer than n
    # counties under-list, mislabelling them.
    over = diverge[diverge["ratio_union_cbp"] > 1].nlargest(8, "ratio_union_cbp")
    under = diverge[diverge["ratio_union_cbp"] < 1].nsmallest(8, "ratio_union_cbp")
    cols = ["county_name", "state_abbr", "obdb_count", "union_count", "cbp_estab", "ratio_union_cbp"]
    print("\n  We list MANY more than CBP (possible stale/duplicate listings, or brewpubs):")
    print(over[cols].round(2).to_string(index=False))
    print(f"\n  We list FEWER than CBP ({len(diverge[diverge['ratio_union_cbp'] < 1])} counties) "
          "-- coverage gaps, since CBP is administrative:")
    print(under[cols].round(2).to_string(index=False) if len(under) else "    (none)")
    print("\n  The second list is the more actionable one: CBP is an administrative")
    print("  count, so a county where it exceeds our union is a county where both")
    print("  crowdsourced sources missed real establishments.")

    d.to_csv(OUT_PATH, index=False)
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
