"""Re-derive per-state capture rates with the OBDB-union-OSM count as the
numerator, instead of OBDB alone.

WHY THIS IS STEP 1 AND NOT STEP 2
---------------------------------
Capture rate and brewery count are not independent: every rate in
`capture_rate_model.CALIBRATED_STATE_CAPTURE_RATES` is defined as
OBDB count / licensee count. Repointing the model's `y` at `union_count` while
those rates still measure OBDB's gap would double-correct -- Georgia's rate of
0.476 implies a ~2.1x upward correction, applied to a numerator that has
already recovered part of that gap through OSM (methods memo 18.13).

So this must run, and its output must be adopted, BEFORE any refit on union
counts. It needs no MCMC: it is the same ratio arithmetic the existing
calibration performs, with a different numerator column.

WHAT THIS DOES NOT DO
---------------------
It does not update `capture_rate_model.py`. It writes the proposed table and
the comparison, so the change can be reviewed before being adopted -- the
constants in that module are hand-curated with per-state documentation of each
registry's quirks, and overwriting them programmatically would discard that.

Writes data/processed/union_capture_rates_proposed.csv.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from breweries.capture_rate_model import CALIBRATED_STATE_CAPTURE_RATES as CAL
from breweries.latent_capture_rate import REGISTRY_STRUCTURALLY_UNDERCOUNTS

CALIBRATION_PATH = "data/processed/pooled_calibration_with_density.parquet"
UNION_PATH = "data/processed/us_county_union_counts.parquet"
ANALYSIS_PATH = "data/processed/us_county_analysis.parquet"
OUT_PATH = "data/processed/union_capture_rates_proposed.csv"


def main() -> None:
    cal = pd.read_parquet(CALIBRATION_PATH)
    union = pd.read_parquet(UNION_PATH)
    analysis = pd.read_parquet(ANALYSIS_PATH)[["county_geoid", "county_name", "state_abbr"]]

    # The calibration frame is keyed by (county_name, state) with the "County"
    # suffix already stripped; the union frame is keyed by GEOID. Bridge them
    # through the analysis frame rather than fuzzy-matching names.
    analysis["join_name"] = (analysis["county_name"]
                             .str.replace(r"\s+(County|Parish|Borough|Census Area|Municipality|city)$",
                                          "", regex=True))
    bridge = analysis.merge(union[["county_geoid", "union_count"]], on="county_geoid", how="left")
    bridge["union_count"] = bridge["union_count"].fillna(0)

    merged = cal.merge(
        bridge[["join_name", "state_abbr", "union_count"]],
        left_on=["county_name", "state"], right_on=["join_name", "state_abbr"], how="left")
    n_unmatched = int(merged["union_count"].isna().sum())
    if n_unmatched:
        print(f"WARNING: {n_unmatched} of {len(cal)} calibration counties did not match a "
              "union count and are excluded from the re-derivation.")
    merged = merged.dropna(subset=["union_count"])

    g = merged.groupby("state").agg(
        obdb=("obdb_count", "sum"), union=("union_count", "sum"),
        licensees=("licensee_count", "sum"))
    g["capture_obdb"] = (g["obdb"] / g["licensees"]).clip(upper=1.0)
    g["capture_union"] = (g["union"] / g["licensees"]).clip(upper=1.0)
    g["raw_ratio_union"] = g["union"] / g["licensees"]
    g["registry_trustworthy"] = ~g.index.isin(REGISTRY_STRUCTURALLY_UNDERCOUNTS)

    print("=" * 76)
    print("PROPOSED capture rates on the union numerator")
    print("=" * 76)
    print(g[["licensees", "obdb", "union", "capture_obdb", "capture_union",
             "raw_ratio_union", "registry_trustworthy"]]
          .round(3).sort_values("capture_union").to_string())

    ok = g[g["registry_trustworthy"]]
    print(f"\n  median capture, trustworthy registries: "
          f"{ok['capture_obdb'].median():.3f} -> {ok['capture_union'].median():.3f}")
    over = ok[ok["raw_ratio_union"] > 1.0]
    print(f"  trustworthy registries now exceeding 1.0 (clipped): {len(over)}/{len(ok)}"
          + (f"  {list(over.index)}" if len(over) else ""))

    print("\n  Interpreting the direction: a capture rate RISES because the numerator")
    print("  grew, not because coverage improved. The correction these rates drive")
    print("  therefore SHRINKS -- which is the whole point. Leaving the old rates in")
    print("  place while switching the count would apply the old, larger correction")
    print("  to an already-larger count.")

    g.reset_index().to_csv(OUT_PATH, index=False)
    print(f"\nWrote {OUT_PATH}")
    print("\nNOT adopted automatically: capture_rate_model.py's constants carry")
    print("per-state documentation of each registry's quirks. Review, then update")
    print("that table and POOLED_CAPTURE_RATE together before refitting.")


if __name__ == "__main__":
    main()
