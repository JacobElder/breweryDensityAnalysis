"""Does the BYM2 spatial term itself cause the downward bias on well-observed
urban counties -- and is it earning its place on the counties the map colours?

MOTIVATION. Adding the missing `log_density` covariate (methods memo Section
18.3) improved held-out log-likelihood and moved Portland and Denver into the
county top 20, but it did NOT fix the bias it was meant to fix:

                             published    + density
  fitted below raw rate         69.4%        67.8%
  median model/raw ratio         0.869        0.885
  top raw-rate quintile cut      31.6%        30.2%
  Fulton GA: 28 observed vs       12.0         12.3   expected

So urbanicity was a real omission but not the whole cause. The remaining
suspect is the spatial prior itself. Posterior rho is 0.971 -- the spatial
effect is almost entirely the STRUCTURED (ICAR) component, whose job is to
pull each county toward its neighbours. Fulton County is an independently
confirmed Gi* COLD spot (z = -3.01): metro Atlanta's OBDB coverage is poor and
its neighbours are low, so the prior pulls Atlanta down regardless of how many
breweries Atlanta itself has.

This script fits the same covariate + state-FE design with and without the
spatial term on the identical seeded split, and scores both STRATIFIED by
county size, to answer the question the pooled average cannot: the spatial
term clearly helps on the ~75% of tiny counties that dominate a pooled score,
but does it help or hurt on the counties that are actually drawn?

Writes data/processed/us_county_spatial_term_urban_bias.csv.
"""

from __future__ import annotations

import os

for _env_var in ("OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMBA_NUM_THREADS"):
    os.environ.setdefault(_env_var, "1")

import numpy as np
import pandas as pd

from fit_combined_spatial_covariate_model import (
    CHAINS,
    DRAWS,
    SEED,
    TARGET_ACCEPT,
    TEST_FRACTION,
    TUNE,
    build_design_matrix,
    compute_bym2_scale,
    fit_nb_model,
    load_conus_graph_with_covariates,
    nb_holdout_loglik_mc,
    posterior_alpha_mu,
    stratified_holdout_loglik,
)

OUT_PATH = "data/processed/us_county_spatial_term_urban_bias.csv"


def bias_summary(df: pd.DataFrame, mu_samples: np.ndarray, label: str,
                 eval_idx: np.ndarray | None = None) -> dict:
    """The same bias diagnostics used in methods memo Section 18.3, recomputed
    from a fit's posterior-median fitted rate.

    `eval_idx` MUST be the held-out fold. Scoring these diagnostics over all
    counties from a train-fold fit invalidates the between-model comparison,
    because the two models are not equally flexible in sample: BYM2 gives every
    county its own free `theta_iid` plus a neighbour-informed `phi_icar`, while
    the no-spatial model has ZERO per-county latent parameters. A model with one
    free parameter per training observation will fit its own training counties
    better whatever its spatial prior is doing, so a pooled diagnostic measures
    flexibility, not the mechanism under test. An earlier version of this script
    pooled train and test here -- 141 of 169 counties were in-sample -- and its
    numbers should not be cited.

    Absolute levels still are not comparable to the production fit (this is a
    train-fold fit; Fulton County GA sits in the test fold). Only the
    BETWEEN-MODEL comparison on the identical split is meaningful.
    """
    fitted_count = np.percentile(mu_samples, 50, axis=0)
    rate = fitted_count / df["adults_21plus"].to_numpy(dtype=float) * 1e5
    raw_rate = df["obdb_rate_per_100k_21plus"].to_numpy(dtype=float)

    mask = (df["obdb_count"].to_numpy() >= 10) & (df["adults_21plus"].to_numpy() >= 50_000)
    if eval_idx is not None:
        held_out = np.zeros(len(df), dtype=bool)
        held_out[eval_idx] = True
        mask &= held_out
    ratio = rate[mask] / raw_rate[mask]
    q5 = raw_rate[mask] >= np.quantile(raw_rate[mask], 0.8)

    well = df["obdb_count"].to_numpy() >= 15
    if eval_idx is not None:
        well &= held_out
    return {
        "model": label,
        "n_wellobs": int(mask.sum()),
        "pct_fitted_below_raw": round(float((ratio < 1).mean()) * 100, 1),
        "median_ratio": round(float(np.median(ratio)), 3),
        "top_quintile_median_ratio": round(float(np.median(ratio[q5])), 3),
        "fulton_ga_expected": round(float(
            fitted_count[df["county_geoid"].to_numpy() == "13121"][0]), 1),
        "mean_abs_log_error_wellobs": round(float(
            np.mean(np.abs(np.log(rate[well] / np.clip(raw_rate[well], 1e-9, None))))), 4),
    }


def main() -> None:
    merged, W = load_conus_graph_with_covariates()
    scale = compute_bym2_scale(W)
    X, _, prior_mu, prior_sigma, is_state_col = build_design_matrix(merged)

    y = merged["obdb_count"].to_numpy(dtype=float)
    log_exposure = np.log(merged["adults_21plus"].to_numpy(dtype=float))

    rng = np.random.default_rng(SEED)
    perm = rng.permutation(len(merged))
    n_test = int(round(len(merged) * TEST_FRACTION))
    test_idx = np.sort(perm[:n_test])
    train_idx = np.sort(perm[n_test:])
    print(f"Train {len(train_idx)} / Test {len(test_idx)} (same seeded split as the main script)")

    rows, bias_rows = [], []
    for spatial_type, label in [("bym2", "covariates + density + state FE + BYM2"),
                                 ("none", "covariates + density + state FE, NO spatial term")]:
        idata = fit_nb_model(
            y, log_exposure, W, scale, train_idx,
            X=X, prior_mu=prior_mu, prior_sigma=prior_sigma, is_state_col=is_state_col,
            spatial_type=spatial_type, draws=DRAWS, tune=TUNE, chains=CHAINS,
            target_accept=TARGET_ACCEPT, label=label,
        )
        alpha_s, mu_s = posterior_alpha_mu(idata, X=X, log_exposure=log_exposure,
                                            scale=scale, spatial_type=spatial_type)
        row = {"model": label,
               "held_out_loglik_per_county": nb_holdout_loglik_mc(y, alpha_s, mu_s, test_idx)}
        row.update(stratified_holdout_loglik(merged, y, alpha_s, mu_s, test_idx))
        rows.append(row)
        bias_rows.append(bias_summary(merged, mu_s, label, eval_idx=test_idx))

    results = pd.DataFrame(rows)
    print("\nHeld-out log-likelihood (higher = better), pooled and by county size:")
    print(results.round(4).to_string(index=False))

    bias = pd.DataFrame(bias_rows)
    print("\nUrban downward-bias diagnostics (Fulton County GA observed 28):")
    print(bias.to_string(index=False))

    merged_out = results.merge(bias, on="model")
    merged_out.to_csv(OUT_PATH, index=False)
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
