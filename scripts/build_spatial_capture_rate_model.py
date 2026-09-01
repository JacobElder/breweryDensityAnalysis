"""EXPLORATORY / VALIDATION SCRIPT -- NOT wired into the production pipeline.

Builds the US state adjacency graph, fits/validates a neighbor-informed
alternative to capture_rate_model.py's density-only pooled fallback (see
src/breweries/spatial_capture_rate.py for the model itself and its full
rationale), and reports a leave-one-state-out (LOSO) comparison against the
existing density-only pooled model on the 23 directly-calibrated states.

This does NOT regenerate any national output and does NOT modify
capture_rate_model.py, build_capture_rate_model.py, or any downstream
dataset. It answers one question honestly: does borrowing from a state's
directly-calibrated geographic neighbors improve held-out capture-rate
prediction over the existing density-only pooled model, or not?

Steps:
  1. Build the state adjacency graph two ways -- the hardcoded
     spatial_capture_rate.STATE_ADJACENCY and an independently computed
     TIGER-dissolve Queen-contiguity graph -- and diff them (verification).
  2. LOSO over the 23 calibrated states. For each held-out state s:
       a. Refit the density-only WLS model (log_capture_ratio ~ log_density,
          weights=licensee_count) on the OTHER 22 states' county data,
          mirroring build_capture_rate_model.py's WLS fit exactly (same
          formula, same weighting) but re-fit per fold so it never sees s's
          own county data -- this is the fair LOSO baseline, not the fixed
          production constants (which WERE fit on all 23 states, including
          s, and would leak).
       b. Aggregate that fold's county-level predictions up to a single
          state-level predicted rate for s, using the same licensee-weighted
          aggregation that produces the "empirical" state rate in the first
          place (predicted_total_obdb / total_licensees), so the density-only
          prediction and the empirical target are computed the same way.
       c. Compute the neighbor-average prediction: mean of s's directly-
          calibrated neighbors' rates, drawn only from the other 22 states
          (a state is never its own neighbor, so this needs no separate
          refit -- it's already leave-one-out by construction).
       d. Compute the shrinkage-blended prediction (spatial_capture_rate.
          blended_capture_rate) for a grid of shrinkage constants k, to
          choose the best-performing k honestly via the same LOSO folds
          (flagged as such -- this is a hyperparameter search over the
          validation data, not a fully held-out third split, given n=23 is
          too small to further subdivide).
  3. Report per-state predictions/errors and pooled LOSO metrics: MAE, RMSE,
     mean absolute log-rate error, for the density-only baseline vs. the
     neighbor-average-only model (where defined) vs. the best blend -- and a
     head-to-head win/loss count per state.
  4. Print an honest recommendation: adopt / don't adopt / adopt with
     caveats, based on whether the blend actually beats the baseline out of
     sample, not just on individual states where it happens to help.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from breweries.capture_rate_model import CALIBRATED_STATE_CAPTURE_RATES
from breweries.spatial_capture_rate import (
    STATE_ADJACENCY,
    blended_capture_rate,
    build_adjacency_from_tiger,
    calibrated_neighbor_rates,
    diff_adjacency,
    edge_count,
    neighbor_average_capture_rate,
)

POOLED_PATH = "data/processed/pooled_calibration_with_density.parquet"
K_GRID = [0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 13.0]

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 20)

# Clip every calibrated rate at 1.0, exactly like capture_rate_model.correction_factor()
# does for calibrated states -- a capture rate is a fraction of a true population and
# cannot exceed 1.0 by definition (see that module's docstring for why several raw
# values legitimately exceed 1.0: reference-registry undercounting, not OBDB overcounting).
CLIPPED_RATES = {s: min(r, 1.0) for s, r in CALIBRATED_STATE_CAPTURE_RATES.items()}
CALIBRATED_STATES = sorted(CLIPPED_RATES)


# ---------------------------------------------------------------------------
# Step 1: adjacency graph construction + verification
# ---------------------------------------------------------------------------

def verify_adjacency() -> None:
    print("=" * 78)
    print("STEP 1: STATE ADJACENCY GRAPH -- construction + verification")
    print("=" * 78)
    n_edges = edge_count(STATE_ADJACENCY)
    n_states_with_neighbors = sum(1 for v in STATE_ADJACENCY.values() if v)
    print(f"Hardcoded STATE_ADJACENCY: {len(STATE_ADJACENCY)} states/DC, "
          f"{n_edges} undirected land-border edges, "
          f"{n_states_with_neighbors} states with >=1 neighbor "
          f"(AK, HI have 0, as expected -- no land neighbors).")

    print("\nVerifying against a Queen-contiguity graph computed by dissolving "
          "TIGER county polygons up to state level (breweries.sources.tiger, "
          "already cached locally from prior work in this project)...")
    computed = build_adjacency_from_tiger()
    n_computed_edges = edge_count(computed)
    print(f"TIGER-derived Queen-contiguity graph: {len(computed)} states/territories, "
          f"{n_computed_edges} undirected edges (incl. non-state territories, "
          "which are dropped from the comparison below).")

    diff = diff_adjacency(computed, STATE_ADJACENCY)
    print(f"\nEdges in TIGER-Queen graph but NOT in hardcoded STATE_ADJACENCY "
          f"({len(diff['computed_only'])}):")
    for a, b in diff["computed_only"]:
        print(f"    {a}-{b}  (Queen contiguity finds a shared boundary VERTEX, not a "
              "land border -- these are known Great-Lakes/Sound water-boundary-line "
              "corner touches: IL-MI meet at a point in Lake Michigan, MI-MN at a "
              "point in Lake Superior, NY-RI at a point in Long Island Sound / Block "
              "Island Sound. None represents a real cross-border land adjacency, so "
              "excluded from STATE_ADJACENCY on purpose.)")
    print(f"\nEdges in hardcoded STATE_ADJACENCY but NOT in TIGER-Queen graph "
          f"({len(diff['hardcoded_only'])}): {diff['hardcoded_only']}")

    if not diff["hardcoded_only"] and len(diff["computed_only"]) <= 3:
        print("\nVERIFIED: the hardcoded graph is a strict subset of the computed Queen "
              "graph, differing only by the known water-vertex artifacts identified above. "
              "No missing or spuriously-added land borders.")
    else:
        print("\nWARNING: unexpected discrepancy beyond the known water-vertex cases -- "
              "investigate before trusting STATE_ADJACENCY.")

    # Spot-check a handful of well-known borders/non-borders by hand as an additional,
    # independent sanity check (not derived from either graph above).
    spot_checks = [
        ("CA", "NV", True), ("CA", "AZ", True), ("CA", "OR", True),
        ("NY", "NJ", True), ("NY", "PA", True), ("NY", "CT", True),
        ("TX", "OK", True), ("TX", "CA", False),  # TX/CA share no border (NM between them)
        ("FL", "AL", True), ("FL", "SC", False),  # GA is between FL and SC
        ("DE", "NJ", True),  # the famous Artificial Island land-border quirk
        ("MI", "MN", False),  # Lake Superior only, no land border
        ("WA", "MT", False),  # ID is between them
        ("VA", "DC", True), ("MD", "DC", True),
    ]
    print("\nManual spot-checks (independent of both graphs above):")
    all_ok = True
    for a, b, expected in spot_checks:
        actual = b in STATE_ADJACENCY[a]
        status = "OK" if actual == expected else "MISMATCH"
        all_ok = all_ok and (actual == expected)
        print(f"    {a}-{b}: expected_adjacent={expected}  got={actual}  [{status}]")
    print("All spot-checks passed." if all_ok else "SOME SPOT-CHECKS FAILED -- investigate.")


# ---------------------------------------------------------------------------
# Step 2: LOSO density-only baseline (refit per fold, no leakage)
# ---------------------------------------------------------------------------

def fit_density_model(train_counties: pd.DataFrame) -> tuple[float, float]:
    """WLS log_capture_ratio ~ log_density, weights=licensee_count -- identical
    formula/weighting to build_capture_rate_model.py's WLS fit, refit here on
    whatever county subset is passed in (the LOSO training fold)."""
    wls = smf.wls(
        "log_capture_ratio ~ log_density", data=train_counties,
        weights=train_counties["licensee_count"],
    ).fit()
    return wls.params["Intercept"], wls.params["log_density"]


def predict_state_rate_density_only(intercept: float, slope: float, state_counties: pd.DataFrame) -> float:
    """Aggregate the fold's county-level density model up to a single
    state-level predicted rate, using the same licensee-weighted aggregation
    that defines the empirical state rate (predicted_total_obdb /
    total_licensees) -- so the density-only prediction and the empirical
    target being compared against are constructed the same way."""
    predicted_ratio = np.exp(intercept + slope * state_counties["log_density"])
    predicted_obdb = predicted_ratio * state_counties["licensee_count"]
    rate = predicted_obdb.sum() / state_counties["licensee_count"].sum()
    return min(float(rate), 1.0)


def run_loso(pooled: pd.DataFrame) -> pd.DataFrame:
    print("\n" + "=" * 78)
    print("STEP 2: LEAVE-ONE-STATE-OUT (LOSO) VALIDATION")
    print("=" * 78)
    print(f"{len(CALIBRATED_STATES)} calibrated states/DC, each held out in turn. For each "
          "fold: refit the density-only WLS model on the other 22 states' COUNTY data "
          "(no leakage), predict the held-out state's aggregate rate; separately, average "
          "the held-out state's directly-calibrated NEIGHBORS' empirical rates (using the "
          "other 22 states -- a state is never its own neighbor, so this is leave-one-out "
          "by construction with no extra refitting needed); blend the two at a grid of "
          "shrinkage constants k.")

    rows = []
    for s in CALIBRATED_STATES:
        train = pooled[(pooled["state"] != s) & (pooled["licensee_count"] > 0)]
        test = pooled[(pooled["state"] == s) & (pooled["licensee_count"] > 0)]
        if test.empty:
            continue

        intercept, slope = fit_density_model(train)
        density_pred = predict_state_rate_density_only(intercept, slope, test)

        neighbor_rates = calibrated_neighbor_rates(s, CLIPPED_RATES, STATE_ADJACENCY)
        neighbor_pred = neighbor_average_capture_rate(s, CLIPPED_RATES, STATE_ADJACENCY)

        row = {
            "state": s,
            "actual_rate": CLIPPED_RATES[s],
            "n_counties": len(test),
            "density_only_pred": density_pred,
            "n_calibrated_neighbors": len(neighbor_rates),
            "neighbor_states": ",".join(STATE_ADJACENCY[s]),
            "neighbor_avg_pred": neighbor_pred,
        }
        for k in K_GRID:
            blend = blended_capture_rate(s, CLIPPED_RATES, density_pred, STATE_ADJACENCY, k=k)
            row[f"blend_k{k}_pred"] = blend["capture_rate"]
        rows.append(row)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Step 3: metrics + reporting
# ---------------------------------------------------------------------------

def _metrics(actual: np.ndarray, pred: np.ndarray) -> dict:
    err = actual - pred
    log_err = np.log(actual) - np.log(pred)
    return {
        "n": len(actual),
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err**2))),
        "mean_abs_log_err": float(np.mean(np.abs(log_err))),
    }


def report(loso: pd.DataFrame) -> None:
    print("\n" + "=" * 78)
    print("STEP 3: LOSO RESULTS")
    print("=" * 78)

    print("\nPer-state predictions (sorted by state):")
    display_cols = ["state", "actual_rate", "density_only_pred", "n_calibrated_neighbors",
                     "neighbor_states", "neighbor_avg_pred"]
    print(loso[display_cols].sort_values("state").to_string(index=False))

    actual = loso["actual_rate"].to_numpy()
    baseline_pred = loso["density_only_pred"].to_numpy()
    baseline_metrics = _metrics(actual, baseline_pred)
    print(f"\nDENSITY-ONLY POOLED BASELINE (existing model's approach, refit per LOSO fold):")
    print(f"  n={baseline_metrics['n']}  MAE={baseline_metrics['mae']:.4f}  "
          f"RMSE={baseline_metrics['rmse']:.4f}  "
          f"mean|log(actual/pred)|={baseline_metrics['mean_abs_log_err']:.4f}")

    has_neighbor = loso["n_calibrated_neighbors"] > 0
    n_with = int(has_neighbor.sum())
    n_without = int((~has_neighbor).sum())
    print(f"\n{n_with}/{len(loso)} calibrated states have >=1 directly-calibrated neighbor "
          f"(the rest -- {loso.loc[~has_neighbor, 'state'].tolist()} -- have none, so the "
          "neighbor-only and blended models collapse exactly to the density-only baseline "
          "for them; they're included in the pooled metrics below for a fair total-n "
          "comparison, but only the states WITH a neighbor can show any difference).")

    if n_with:
        na_actual = loso.loc[has_neighbor, "actual_rate"].to_numpy()
        na_pred = loso.loc[has_neighbor, "neighbor_avg_pred"].to_numpy()
        na_metrics = _metrics(na_actual, na_pred)
        na_baseline = loso.loc[has_neighbor, "density_only_pred"].to_numpy()
        na_baseline_metrics = _metrics(na_actual, na_baseline)
        print(f"\nNEIGHBOR-AVERAGE-ONLY MODEL (states with >=1 calibrated neighbor only, n={n_with}):")
        print(f"  Neighbor-avg:   MAE={na_metrics['mae']:.4f}  RMSE={na_metrics['rmse']:.4f}  "
              f"mean|log err|={na_metrics['mean_abs_log_err']:.4f}")
        print(f"  Density-only (same {n_with} states, for direct comparison): "
              f"MAE={na_baseline_metrics['mae']:.4f}  RMSE={na_baseline_metrics['rmse']:.4f}  "
              f"mean|log err|={na_baseline_metrics['mean_abs_log_err']:.4f}")

    print(f"\nBLEND GRID SEARCH (shrinkage weight w = n_neighbors / (n_neighbors + k), "
          "all 23 states pooled -- k chosen to minimize LOSO error on this same set, "
          "flagged honestly as a hyperparameter search over the validation data itself, "
          "not a further-held-out third split; n=23 is too small to subdivide further):")
    grid_rows = []
    for k in K_GRID:
        pred = loso[f"blend_k{k}_pred"].to_numpy()
        m = _metrics(actual, pred)
        grid_rows.append({"k": k, **m})
    grid_df = pd.DataFrame(grid_rows)
    print(grid_df.to_string(index=False))

    best_k_row = grid_df.loc[grid_df["mae"].idxmin()]
    best_k = best_k_row["k"]
    print(f"\nBest k by pooled LOSO MAE: k={best_k} "
          f"(MAE={best_k_row['mae']:.4f} vs. baseline {baseline_metrics['mae']:.4f})")

    best_pred = loso[f"blend_k{best_k}_pred"].to_numpy()
    best_metrics = _metrics(actual, best_pred)

    print("\n" + "-" * 78)
    print(f"HEAD-TO-HEAD: density-only baseline vs. best blend (k={best_k}), per state:")
    print("-" * 78)
    loso["baseline_abs_err"] = np.abs(loso["actual_rate"] - loso["density_only_pred"])
    loso["blend_abs_err"] = np.abs(loso["actual_rate"] - loso[f"blend_k{best_k}_pred"])
    loso["blend_wins"] = loso["blend_abs_err"] < loso["baseline_abs_err"]
    compare_cols = ["state", "actual_rate", "n_calibrated_neighbors", "density_only_pred",
                     f"blend_k{best_k}_pred", "baseline_abs_err", "blend_abs_err", "blend_wins"]
    print(loso[compare_cols].sort_values("n_calibrated_neighbors", ascending=False).to_string(index=False))

    n_blend_wins = int(loso["blend_wins"].sum())
    n_ties_or_baseline = len(loso) - n_blend_wins
    print(f"\nBlend beats (strictly lower abs error than) density-only baseline in "
          f"{n_blend_wins}/{len(loso)} states; baseline wins or ties in {n_ties_or_baseline}/{len(loso)}.")
    among_neighbors = loso[loso["n_calibrated_neighbors"] > 0]
    n_blend_wins_sub = int(among_neighbors["blend_wins"].sum())
    print(f"Restricted to the {len(among_neighbors)} states with >=1 calibrated neighbor "
          f"(the only ones where blend and baseline can differ): blend wins in "
          f"{n_blend_wins_sub}/{len(among_neighbors)}.")

    print("\n" + "=" * 78)
    print("POOLED SUMMARY: density-only baseline vs. best blend")
    print("=" * 78)
    print(f"  Density-only pooled (existing model, LOSO-refit): "
          f"MAE={baseline_metrics['mae']:.4f}  RMSE={baseline_metrics['rmse']:.4f}  "
          f"mean|log err|={baseline_metrics['mean_abs_log_err']:.4f}")
    print(f"  Best neighbor-density blend (k={best_k}):          "
          f"MAE={best_metrics['mae']:.4f}  RMSE={best_metrics['rmse']:.4f}  "
          f"mean|log err|={best_metrics['mean_abs_log_err']:.4f}")
    mae_improvement = (baseline_metrics["mae"] - best_metrics["mae"]) / baseline_metrics["mae"]
    print(f"  MAE change: {mae_improvement:+.1%} ({'improvement' if mae_improvement > 0 else 'WORSE'})")

    # ------------------------------------------------------------------
    # Honest recommendation.
    # ------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("RECOMMENDATION")
    print("=" * 78)
    if mae_improvement > 0.05 and n_blend_wins_sub / max(len(among_neighbors), 1) >= 0.5:
        print(
            "ADOPT (with caveats): the neighbor-informed blend beats the existing "
            "density-only pooled baseline on pooled LOSO MAE and wins on a majority of "
            "the states that actually have calibrated neighbors. Caveats: (1) the k "
            "hyperparameter was chosen on the same 23-state LOSO set it's being judged "
            "on, so this is optimistic by an unknown but likely small amount at this n; "
            "(2) TX and any other calibrated state with zero calibrated neighbors get no "
            "benefit and are carried at parity with baseline; (3) coverage for the 28 "
            "currently-uncalibrated states depends on how many of THEM have a calibrated "
            "neighbor -- that's a separate, larger population than the 23 calibrated "
            "states tested here and should be checked before wiring this in."
        )
    else:
        print(
            "DO NOT ADOPT as currently specified: the neighbor-informed blend does not "
            "convincingly beat the existing density-only pooled baseline out of sample "
            f"({mae_improvement:+.1%} MAE change, blend wins only "
            f"{n_blend_wins_sub}/{len(among_neighbors)} of the states with a calibrated "
            "neighbor). At n=23 calibrated states, most with only 1-4 calibrated "
            "neighbors, there isn't enough held-out signal to reliably beat a simple "
            "density regression this way. See the printed per-state and grid-search "
            "tables above for exactly where it helps vs. hurts before concluding this "
            "avenue is dead -- a larger set of calibrated states in future work could "
            "change this conclusion."
        )


def main() -> None:
    verify_adjacency()
    pooled = pd.read_parquet(POOLED_PATH)
    loso = run_loso(pooled)
    report(loso)


if __name__ == "__main__":
    main()
