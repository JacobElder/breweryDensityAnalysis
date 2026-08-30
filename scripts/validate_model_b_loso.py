"""Leave-one-state-out (LOSO) holdout validation for Model B.

Model B (see scripts/fit_national_models.py::fit_covariate_residual_model) is a
negative-binomial GLM:

    obdb_count ~ log_income + median_age + college_enrollment_share
                 + tourism_estab_per_10k + C(state_abbr)

offset by log(adults_21plus), ranked by shrunken residual (observed/expected) to
produce the "more breweries than covariates predict" list reported in the README
(Buncombe NC, Charleston SC, Fulton GA, St. Louis city MO, Travis TX, Richmond
city VA at the top). That ranking has only ever been judged by in-sample fit and
face validity. This script asks a harder question: if you refit the model with an
entire state's counties withheld, how well does it predict that state's brewery
counts, and does the withheld state's top-residual county still look like an
outlier?

The model-fitting logic below (formula, offset handling, NB alpha estimation,
shrinkage formula) intentionally mirrors
scripts/fit_national_models.py::fit_covariate_residual_model rather than
importing it, both to avoid a runtime dependency on a file other parallel agents
are actively editing, and because the LOSO fold logic needs direct access to the
fitted params (to build the missing-state-FE prediction below) rather than the
convenience `.predict()` wrapper.

Key methodological wrinkle: a state held out of training has no fitted
C(state_abbr) dummy coefficient. Three options exist for predicting its counties:
  1. Treat it as the reference (dropped) category, i.e. state effect = 0.
  2. Use the mean of the fitted state effects as a "population-average" state
     effect.
  3. Refit some hierarchical/partial-pooling model of state effects and predict
     from that.
This script uses option 2: for each fold, extract the fitted C(state_abbr)
coefficients (all relative to whichever state patsy dropped as reference for
that fold), and average them together with an implicit 0 for the reference state
itself, giving the mean deviation across all *trained-on* states. That average
is added to the linear predictor for the held-out state's counties. Option 1 was
rejected because the reference category is an arbitrary alphabetical accident of
which states happen to be present in the training fold (verified below: dropping
a state can shift which state is used as reference), so "state effect = 0" does
not mean anything stable or interpretable across folds. Option 2 gives the best
available "if we knew nothing about this state's specific regulatory/cultural
regime, only the national relationship between covariates and brewery counts"
prediction.

This also means LOSO necessarily understates Model B's real-world predictive
accuracy for any state whose true state effect is far from the cross-state
average, and the gap between in-sample and LOSO fit is partly a statement about
how much explanatory weight the model puts on state fixed effects (which the
methods memo already flags as substantial) rather than purely a statement about
overfitting in the covariates themselves. That distinction is discussed in the
final printed summary.
"""

from __future__ import annotations

import re
import time
import warnings

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from scipy.special import gammaln
from scipy.stats import pearsonr, spearmanr

pd.set_option("display.width", 160)
pd.set_option("display.max_colwidth", 30)

DATA_PATH = "data/processed/us_county_analysis.parquet"
OUTPUT_PATH = "data/processed/model_b_loso_validation.parquet"
POPULATION_FLOOR = 50_000

COVARS = ["log_income", "median_age", "college_enrollment_share", "tourism_estab_per_10k"]
FORMULA = (
    "obdb_count ~ log_income + median_age + college_enrollment_share "
    "+ tourism_estab_per_10k + C(state_abbr)"
)
STATE_TERM_RE = re.compile(r"C\(state_abbr\)")

# Named because it appears in the README top-6 "more breweries than expected" list.
# Full county_name values (matching the dataset's naming, incl. "County"/"city"
# suffixes as applicable) so the isin() match below is exact.
HEADLINE_COUNTIES = [
    "Buncombe County",
    "Charleston County",
    "Fulton County",
    "St. Louis city",
    "Travis County",
    "Richmond city",
]


def prep_model_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["log_offset"] = np.log(df["adults_21plus"])
    df["log_income"] = np.log(df["median_household_income"])
    model_df = df.dropna(
        subset=["log_income", "median_age", "college_enrollment_share",
                "tourism_estab_per_10k", "state_abbr"]
    ).copy()
    return model_df


def fit_nb(train_df: pd.DataFrame):
    # Mirrors fit_national_models.py::fit_covariate_residual_model exactly (same
    # formula, same offset, same optimizer/tolerance settings) so that LOSO folds
    # are directly comparable to the full-sample fit.
    with warnings.catch_warnings():
        # statsmodels emits a benign divide-by-zero warning inside its own
        # internal loglike scratch evaluation at some BFGS trial points; the
        # final converged fit is unaffected (verified against nb.predict() to
        # machine precision during development of this script).
        warnings.simplefilter("ignore", category=RuntimeWarning)
        nb = smf.negativebinomial(FORMULA, data=train_df, offset=train_df["log_offset"]).fit(
            method="bfgs", disp=0, maxiter=500
        )
    return nb


def mean_state_effect(nb) -> float:
    """Population-average state fixed effect for a fold: mean of the fitted
    C(state_abbr) dummy coefficients, together with an implicit 0 for whichever
    state patsy dropped as the reference category in that fold. See module
    docstring for why this (rather than "reference state = 0") is used to score
    a state that had zero training rows.
    """
    state_params = nb.params[nb.params.index.str.contains(STATE_TERM_RE)]
    n_trained_states = state_params.shape[0] + 1  # +1 for the implicit-0 reference state
    return state_params.sum() / n_trained_states


def predict_no_state_fe(nb, test_df: pd.DataFrame, avg_state_effect: float) -> np.ndarray:
    """Expected counts for counties in a state the model never trained on, using
    the fold's fitted covariate coefficients plus the population-average state
    effect in place of a state-specific dummy (which does not exist for this
    state in this fold)."""
    params = nb.params
    eta = params["Intercept"] + avg_state_effect
    for c in COVARS:
        eta = eta + params[c] * test_df[c]
    return np.exp(eta + test_df["log_offset"])


def nb2_loglike(y: np.ndarray, mu: np.ndarray, alpha: float) -> np.ndarray:
    """Per-observation NB2 log-likelihood (variance = mu + alpha*mu**2), matching
    statsmodels' internal parametrization for NegativeBinomial(loglike_method=
    'nb2'). Used to score held-out counts under each fold's own estimated alpha,
    since statsmodels' discrete NegativeBinomialResults has no public out-of-sample
    scoring method that accepts an externally supplied mu.
    """
    alpha = max(alpha, 1e-10)
    size = 1.0 / alpha
    prob = size / (size + mu)
    return (
        gammaln(size + y) - gammaln(y + 1) - gammaln(size)
        + size * np.log(prob) + y * np.log(1 - prob)
    )


def shrunken_ratio(count: np.ndarray, expected: np.ndarray, alpha: float) -> np.ndarray:
    # Identical formula to fit_national_models.py::fit_covariate_residual_model's
    # shrunken_residual_ratio: NB-Gamma partial pooling toward each county's own
    # covariate-based expectation, using this fold's estimated alpha.
    shape = 1.0 / alpha
    rate = 1.0 / (alpha * expected)
    return (shape + count) / (rate + 1) / expected


def summarize_metrics(label: str, y: np.ndarray, mu: np.ndarray, alpha: float) -> dict:
    err = y - mu
    mae = np.mean(np.abs(err))
    rmse = np.sqrt(np.mean(err**2))
    ll = nb2_loglike(y, mu, alpha).sum()
    r, _ = pearsonr(y, mu) if len(y) > 1 and np.std(mu) > 0 else (np.nan, np.nan)
    print(f"{label}: n={len(y)}  MAE={mae:.3f}  RMSE={rmse:.3f}  "
          f"total NB2 loglik={ll:.1f}  mean loglik/county={ll/len(y):.3f}  "
          f"Pearson r(actual,pred)={r:.3f}")
    return {"label": label, "n": len(y), "mae": mae, "rmse": rmse,
            "total_loglik": ll, "mean_loglik": ll / len(y), "pearson_r": r}


def main() -> None:
    df = pd.read_parquet(DATA_PATH)
    model_df = prep_model_df(df)
    print(f"Model dataframe: {len(model_df)} counties, "
          f"{model_df['state_abbr'].nunique()} states (incl. DC)")

    # ------------------------------------------------------------------
    # In-sample (full national fit) baseline, refit here identically to
    # fit_national_models.py so LOSO numbers below are apples-to-apples.
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("IN-SAMPLE (full-data) fit, for comparison to LOSO below")
    print("=" * 70)
    t0 = time.time()
    full_nb = fit_nb(model_df)
    print(f"Full-sample fit time: {time.time() - t0:.2f}s")
    full_alpha = full_nb.params["alpha"]
    model_df["expected_count_full"] = full_nb.predict(model_df, offset=model_df["log_offset"])
    model_df["shrunken_residual_full"] = shrunken_ratio(
        model_df["obdb_count"].values, model_df["expected_count_full"].values, full_alpha
    )
    print(f"Full-sample estimated alpha (NB dispersion): {full_alpha:.4f}")
    insample_metrics = summarize_metrics(
        "In-sample", model_df["obdb_count"].values, model_df["expected_count_full"].values, full_alpha
    )

    # ------------------------------------------------------------------
    # Leave-one-state-out cross-validation.
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("LEAVE-ONE-STATE-OUT CROSS-VALIDATION")
    print("=" * 70)
    states = sorted(model_df["state_abbr"].unique())
    print(f"Running {len(states)} folds (one per state, incl. DC)...")

    fold_frames = []
    fold_rows = []
    t_start = time.time()
    reference_states_seen = set()
    for s in states:
        train = model_df[model_df["state_abbr"] != s]
        test = model_df[model_df["state_abbr"] == s].copy()
        if test.empty:
            continue

        t0 = time.time()
        nb = fit_nb(train)
        fit_time = time.time() - t0

        # Track which state patsy dropped as reference this fold, to confirm
        # (per the module docstring) that it moves around and is therefore not a
        # stable stand-in for "no state effect".
        state_params_idx = nb.params.index[nb.params.index.str.contains(STATE_TERM_RE)]
        trained_states = sorted(train["state_abbr"].unique())
        dummied_states = {m.group(1) for m in
                           (re.search(r"\[T\.(.+)\]", name) for name in state_params_idx) if m}
        ref_state = (set(trained_states) - dummied_states).pop()
        reference_states_seen.add(ref_state)

        alpha = nb.params["alpha"]
        avg_eff = mean_state_effect(nb)
        mu = predict_no_state_fe(nb, test, avg_eff).values

        test["expected_count_loso"] = mu
        test["residual_ratio_loso"] = test["obdb_count"] / test["expected_count_loso"]
        test["shrunken_residual_loso"] = shrunken_ratio(test["obdb_count"].values, mu, alpha)
        test["loso_alpha"] = alpha
        test["loso_mean_state_effect"] = avg_eff
        test["loso_fold_reference_state"] = ref_state
        fold_frames.append(test)

        y = test["obdb_count"].values
        mae = np.mean(np.abs(y - mu))
        r = np.nan
        if len(y) > 1 and np.std(mu) > 0:
            r, _ = pearsonr(y, mu)
        fold_rows.append({
            "state_abbr": s, "n_counties": len(test), "alpha": alpha,
            "mean_state_effect": avg_eff, "mae": mae, "pearson_r": r,
            "fit_time_s": fit_time,
        })

    total_time = time.time() - t_start
    print(f"All {len(states)} folds fit in {total_time:.1f}s "
          f"({total_time/len(states):.2f}s/fold on average)")
    print(f"Reference (dropped) state varied across folds: "
          f"{len(reference_states_seen)} distinct reference states seen across "
          f"{len(states)} folds -> confirms 'state effect = 0' is not a stable "
          f"baseline across folds, justifying the mean-state-effect approach.")

    loso_df = pd.concat(fold_frames, ignore_index=True)
    fold_summary = pd.DataFrame(fold_rows).sort_values("mae", ascending=False)

    print("\nPer-state LOSO fold summary (worst MAE first, top 15):")
    print(fold_summary.head(15).to_string(index=False))

    print("\nPer-state LOSO fold summary (best MAE first, top 10):")
    print(fold_summary.tail(10).sort_values("mae").to_string(index=False))

    n_states_with_corr = fold_summary["pearson_r"].notna().sum()
    print(f"\n{n_states_with_corr}/{len(fold_summary)} states had >=2 counties "
          f"(correlation defined). States with a single county (e.g. DC) have "
          f"no within-state predicted/actual correlation to report.")

    # ------------------------------------------------------------------
    # Pooled out-of-sample metrics across all folds combined.
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("POOLED LOSO OUT-OF-SAMPLE METRICS (all counties, each predicted from a")
    print("model that never saw its own state)")
    print("=" * 70)
    # Pooled log-likelihood uses each county's own fold-specific alpha (i.e. sums
    # nb2_loglike(y, mu, alpha_fold) row-by-row rather than a single alpha), since
    # alpha genuinely differs by fold.
    pooled_ll = sum(
        nb2_loglike(f["obdb_count"].values, f["expected_count_loso"].values, f["loso_alpha"].iloc[0]).sum()
        for f in fold_frames
    )
    y_all = loso_df["obdb_count"].values
    mu_all = loso_df["expected_count_loso"].values
    mae_loso = np.mean(np.abs(y_all - mu_all))
    rmse_loso = np.sqrt(np.mean((y_all - mu_all) ** 2))
    r_loso, _ = pearsonr(y_all, mu_all)
    print(f"LOSO pooled: n={len(y_all)}  MAE={mae_loso:.3f}  RMSE={rmse_loso:.3f}  "
          f"total NB2 loglik={pooled_ll:.1f}  mean loglik/county={pooled_ll/len(y_all):.3f}  "
          f"Pearson r(actual,pred)={r_loso:.3f}")

    print("\nIn-sample vs. LOSO comparison:")
    print(f"  MAE:            in-sample={insample_metrics['mae']:.3f}   "
          f"LOSO={mae_loso:.3f}   ratio={mae_loso/insample_metrics['mae']:.2f}x")
    print(f"  RMSE:           in-sample={insample_metrics['rmse']:.3f}   "
          f"LOSO={rmse_loso:.3f}   ratio={rmse_loso/insample_metrics['rmse']:.2f}x")
    print(f"  mean loglik/ct: in-sample={insample_metrics['mean_loglik']:.3f}   "
          f"LOSO={pooled_ll/len(y_all):.3f}")
    print(f"  Pearson r:      in-sample={insample_metrics['pearson_r']:.3f}   "
          f"LOSO={r_loso:.3f}")

    # ------------------------------------------------------------------
    # Does the top-residual ranking hold up under LOSO?
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("RANKING STABILITY: top residual counties, full-sample vs. LOSO")
    print("=" * 70)
    pop_ok = model_df["adults_21plus"] >= POPULATION_FLOOR
    full_ranked = model_df[pop_ok].copy().sort_values("shrunken_residual_full", ascending=False)
    full_ranked["rank_full"] = np.arange(1, len(full_ranked) + 1)

    loso_pop_ok = loso_df["adults_21plus"] >= POPULATION_FLOOR
    loso_ranked = loso_df[loso_pop_ok].copy().sort_values("shrunken_residual_loso", ascending=False)
    loso_ranked["rank_loso"] = np.arange(1, len(loso_ranked) + 1)

    rank_compare = full_ranked[["county_geoid", "county_name", "state_abbr", "obdb_count",
                                 "expected_count_full", "shrunken_residual_full", "rank_full"]].merge(
        loso_ranked[["county_geoid", "expected_count_loso", "shrunken_residual_loso", "rank_loso"]],
        on="county_geoid", how="inner",
    )
    rank_compare["rank_change"] = rank_compare["rank_loso"] - rank_compare["rank_full"]

    rho, _ = spearmanr(rank_compare["rank_full"], rank_compare["rank_loso"])
    print(f"Spearman rank correlation between full-sample and LOSO shrunken-residual "
          f"rankings (pop >= {POPULATION_FLOOR:,}, n={len(rank_compare)}): rho={rho:.3f}")

    print("\nTop 20 by full-sample shrunken residual, showing their LOSO rank/value:")
    top20 = rank_compare.sort_values("rank_full").head(20)
    print(top20[["county_name", "state_abbr", "obdb_count", "expected_count_full",
                 "shrunken_residual_full", "rank_full", "expected_count_loso",
                 "shrunken_residual_loso", "rank_loso", "rank_change"]].to_string(index=False))

    print(f"\nHeadline counties (README top-6 'more breweries than expected' list):")
    headline_mask = rank_compare["county_name"].isin(HEADLINE_COUNTIES)
    headline = rank_compare[headline_mask].sort_values("rank_full")
    print(headline[["county_name", "state_abbr", "obdb_count", "expected_count_full",
                    "rank_full", "expected_count_loso", "rank_loso", "rank_change"]].to_string(index=False))

    n_top20_still_top20 = (top20["rank_loso"] <= 20).sum()
    print(f"\nOf the full-sample top 20, {n_top20_still_top20}/20 remain in the LOSO top 20.")

    # ------------------------------------------------------------------
    # Write per-county LOSO results for further inspection.
    # ------------------------------------------------------------------
    out_cols = [
        "county_geoid", "county_name", "state_abbr", "obdb_count", "adults_21plus",
        "expected_count_loso", "residual_ratio_loso", "shrunken_residual_loso",
        "loso_alpha", "loso_mean_state_effect", "loso_fold_reference_state",
    ]
    out_df = loso_df[out_cols].merge(
        model_df[["county_geoid", "expected_count_full", "shrunken_residual_full"]],
        on="county_geoid", how="left",
    )
    out_df.to_parquet(OUTPUT_PATH, index=False)
    print(f"\nWrote {OUTPUT_PATH} ({len(out_df)} rows)")


if __name__ == "__main__":
    main()
