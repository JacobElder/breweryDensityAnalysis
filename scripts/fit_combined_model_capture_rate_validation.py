"""Does refitting the adopted combined model (covariates + state FE + BYM2)
on capture-rate-corrected counts beat naive post-hoc scaling of the existing
raw-count model? Answers a user question raised after sharing the headline
choropleth: "the correction seems like it would be an improvement, why isn't
it merged into the adopted model?"

Two candidate ways to bring the capture-rate correction
(src/breweries/capture_rate_model.py, applied per-county in
us_county_analysis.parquet as obdb_corrected/capture_rate) into the adopted
model:

  1. POST-HOC SCALING: keep the existing raw-count fit, divide its predicted
     rate by each county's capture_rate after the fact. Cheap, but wrong in
     general -- the model's spatial/covariate smoothing is nonlinear (log
     link, ICAR/iid mixing), so scaling the OUTPUT of a nonlinear smoother by
     a county-specific constant is not the same as smoothing already-scaled
     INPUTS. It also reuses the raw fit's dispersion (alpha), estimated on
     the wrong count scale.
  2. PROPER REFIT: fit the exact same model spec directly on
     obdb_corrected_rounded (rounded because NegativeBinomial needs integer
     counts and no continuous generalization of the shrinkage/NB machinery
     exists here -- same rounding build_corrected_rankings.py uses for
     Model A's corrected variant).

This script settles it empirically rather than by intuition: fit BOTH (1)'s
ingredients and (2) on the identical train fold of the same seeded 80/20
split fit_combined_spatial_covariate_model.py uses, then score both against
the SAME held-out target -- the corrected count on the test fold -- so the
comparison is apples-to-apples (scoring one model on raw counts and the
other on corrected counts would make a "which target is easier to predict"
comparison, not a "which method is better" comparison).

Also checks a distinct redundancy concern the user's question implied:
capture-rate correction is itself a state-level adjustment (calibrated
per-state, or pooled+density-extrapolated for 28 states) -- so does
pre-correcting counts make the model's OWN state fixed effects collapse
toward zero (evidence the correction and state FE were capturing the same
thing), or do they stay distinct (evidence they capture different things:
state FE = residual state-level brewery density after covariates; capture-
rate correction = OBDB's per-state undercount, a data-quality artifact
unrelated to true density)?

Per the project's validate-before-adopt convention (see methods_memo.md
Section 15), the corrected-count production fit only gets run if this
comparison shows a real win -- see the printed recommendation at the end.

Outputs:
  data/processed/us_county_combined_capture_rate_validation.csv -- the 2-row
    held-out comparison (post-hoc-scaled raw vs. direct corrected-count fit).
  data/processed/us_county_combined_state_fe_redundancy.csv -- per-state
    comparison of state FE coefficients (raw-count fit vs. corrected-count
    fit) against each state's own log(1/capture_rate) adjustment.
"""

from __future__ import annotations

# --- Must be set before numpy/pymc/pytensor are imported -------------------
# See fit_combined_spatial_covariate_model.py's module docstring: Accelerate's
# multithreaded BLAS segfaults inside PyMC's multiprocessing worker pool on
# this machine unless every worker is pinned to a single BLAS thread.
import os

for _env_var in ("OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMBA_NUM_THREADS"):
    os.environ.setdefault(_env_var, "1")

import time
import warnings

import arviz as az
import numpy as np
import pandas as pd
import patsy
import pymc as pm
from libpysal.weights import Queen
from scipy import stats
from scipy.special import logsumexp

from breweries.sources import tiger

ANALYSIS_PATH = "data/processed/us_county_analysis.parquet"
OUT_HOLDOUT_COMPARISON = "data/processed/us_county_combined_capture_rate_validation.csv"
OUT_STATE_FE_REDUNDANCY = "data/processed/us_county_combined_state_fe_redundancy.csv"

TERRITORY_FIPS = {"02", "15", "72", "78", "60", "66", "69"}
COVARIATE_COLS = [
    "log_income", "median_age", "college_enrollment_share", "tourism_estab_per_10k",
    "pop_growth_pct", "unemployment_rate", "median_gross_rent",
]
SEED = 42
TEST_FRACTION = 0.20

# Same CV settings fit_combined_spatial_covariate_model.py uses for its
# holdout folds -- sufficient for a predictive-accuracy comparison, not
# intended to produce publishable per-coefficient posteriors.
DRAWS, TUNE, CHAINS, TARGET_ACCEPT = 2000, 2000, 4, 0.95

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 20)


# ---------------------------------------------------------------------------
# Data + graph (identical construction to fit_combined_spatial_covariate_model.py)
# ---------------------------------------------------------------------------

def load_conus_graph_with_covariates() -> tuple[pd.DataFrame, np.ndarray]:
    counties = tiger.load_counties()[["STATEFP", "GEOID", "NAMELSAD", "geometry"]]
    conus = counties[~counties["STATEFP"].isin(TERRITORY_FIPS)].copy()
    conus = conus.to_crs(epsg=5070).reset_index(drop=True)

    df = pd.read_parquet(ANALYSIS_PATH)
    df["county_geoid"] = df["county_geoid"].str.zfill(5)

    merged = conus.merge(df, left_on="GEOID", right_on="county_geoid", how="inner").reset_index(drop=True)
    match_rate = len(merged) / len(conus)
    print(f"CONUS counties matched to analysis dataset: {len(merged)} / {len(conus)} ({match_rate:.1%})")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        w = Queen.from_dataframe(merged, use_index=False)
    if w.islands:
        raise RuntimeError(f"{len(w.islands)} island counties -- ICAR requires a fully connected graph.")
    W = w.full()[0].astype(int)

    merged["log_income"] = np.log(merged["median_household_income"])
    raw_cov_cols = ["log_income", "median_age", "college_enrollment_share", "tourism_estab_per_10k",
                     "pop_growth_pct", "unemployment_rate", "median_gross_rent"]
    for col in raw_cov_cols:
        n_na = merged[col].isna().sum()
        if n_na:
            merged[col] = merged[col].fillna(merged[col].median())

    assert merged["obdb_corrected"].notna().all(), "obdb_corrected should be precomputed for every CONUS county"
    assert merged["capture_rate"].notna().all(), "capture_rate should be precomputed for every CONUS county"
    merged["obdb_corrected_rounded"] = merged["obdb_corrected"].round().astype(int)

    return merged, W


def compute_bym2_scale(W: np.ndarray) -> float:
    N = W.shape[0]
    Q = np.diag(W.sum(axis=1).astype(float)) - W.astype(float)
    v = np.ones(N) / np.sqrt(N)
    Q_star = Q + np.outer(v, v)
    Q_star_inv = np.linalg.inv(Q_star)
    Q_plus_diag = np.diag(Q_star_inv) - v ** 2
    return float(np.exp(np.mean(np.log(Q_plus_diag))))


def build_design_matrix(df: pd.DataFrame) -> tuple[np.ndarray, list[str], np.ndarray, np.ndarray, np.ndarray]:
    df = df.copy()
    for col in COVARIATE_COLS:
        df[col] = (df[col] - df[col].mean()) / df[col].std()

    formula = "0 + " + " + ".join(COVARIATE_COLS) + " + C(state_abbr)"
    design = patsy.dmatrix(formula, data=df, return_type="dataframe")
    X = design.values.astype(float)
    colnames = list(design.columns)

    is_state_col = np.array([c.startswith("C(state_abbr)") for c in colnames])
    prior_mu = np.zeros(len(colnames))
    prior_sigma = np.where(is_state_col, 2.0, 1.0)
    return X, colnames, prior_mu, prior_sigma, is_state_col


# ---------------------------------------------------------------------------
# BYM2 NB fit (spatial_type fixed to "bym2" -- this script only needs that variant)
# ---------------------------------------------------------------------------

def fit_bym2_nb_model(
    y: np.ndarray, log_exposure: np.ndarray, W: np.ndarray, scale: float, train_idx: np.ndarray,
    *, X: np.ndarray, prior_mu: np.ndarray, prior_sigma: np.ndarray, is_state_col: np.ndarray,
    draws: int, tune: int, chains: int, target_accept: float, label: str,
) -> az.InferenceData:
    N = len(y)
    train_mean_log_rate = float(np.log(y[train_idx].sum() / np.exp(log_exposure[train_idx]).sum()))

    with pm.Model():
        mu_vec = np.where(is_state_col, train_mean_log_rate, prior_mu)
        beta = pm.Normal("beta", mu=mu_vec, sigma=prior_sigma, shape=X.shape[1])
        linpred = pm.math.dot(X, beta)

        alpha = pm.Exponential("alpha", 1)

        sigma_bym = pm.HalfNormal("sigma_bym", sigma=2)
        rho = pm.Beta("rho", 1, 1)
        phi_icar = pm.ICAR("phi_icar", W=W)
        theta_iid = pm.Normal("theta_iid", mu=0, sigma=1, shape=N)
        spatial_term = sigma_bym * (
            pm.math.sqrt(rho / scale) * phi_icar + pm.math.sqrt(1 - rho) * theta_iid
        )

        log_mu = log_exposure + linpred + spatial_term
        mu_full = pm.Deterministic("mu_full", pm.math.exp(log_mu))
        pm.NegativeBinomial("obs", mu=mu_full[train_idx], alpha=alpha, observed=y[train_idx])

        t0 = time.time()
        idata = pm.sample(
            draws=draws, tune=tune, chains=chains, cores=min(chains, 4),
            target_accept=target_accept, random_seed=SEED, progressbar=False,
        )
        elapsed = time.time() - t0

    divergences = int(idata.sample_stats.diverging.sum())
    summ = az.summary(idata, var_names=["alpha", "sigma_bym", "rho"])
    beta_summ = az.summary(idata, var_names=["beta"])
    phi_summ = az.summary(idata, var_names=["phi_icar"])
    theta_summ = az.summary(idata, var_names=["theta_iid"])
    max_rhat = max(summ["r_hat"].max(), beta_summ["r_hat"].max(), phi_summ["r_hat"].max(), theta_summ["r_hat"].max())

    print(f"  [{label}] {N} counties ({len(train_idx)} in likelihood), {draws}x{chains} chains "
          f"in {elapsed:.0f}s | divergences={divergences} | max rhat={max_rhat:.4f}")
    if divergences > 0 or max_rhat > 1.05:
        print(f"  [{label}] WARNING: convergence looks shaky -- treat results with caution.")
    elif max_rhat > 1.01:
        print(f"  [{label}] NOTE: rhat max {max_rhat:.4f} above the strict 1.01 bar but below 1.05 tolerance.")

    return idata


def posterior_alpha_mu_beta(idata: az.InferenceData) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    post = idata.posterior
    alpha_samples = post["alpha"].values.reshape(-1)
    mu_samples = post["mu_full"].values.reshape(-1, post["mu_full"].shape[-1])
    beta_samples = post["beta"].values.reshape(-1, post["beta"].shape[-1])
    return alpha_samples, mu_samples, beta_samples


def nb_holdout_loglik_mc_prescaled(y_test: np.ndarray, alpha_samples: np.ndarray, mu_test_samples: np.ndarray) -> float:
    """Same MC posterior-predictive evaluator as fit_combined_spatial_covariate_model.py's
    nb_holdout_loglik_mc, but takes mu already sliced/scaled by the caller --
    needed here because the post-hoc-scaled comparison divides mu by each
    county's capture_rate before scoring, which plain indexing can't express.
    """
    alpha = alpha_samples[:, None]
    p = alpha / (alpha + mu_test_samples)
    logpmf = stats.nbinom.logpmf(y_test[None, :], n=alpha, p=p)
    S = logpmf.shape[0]
    log_post_pred = logsumexp(logpmf, axis=0) - np.log(S)
    return float(np.mean(log_post_pred))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print("Capture-rate correction validation: post-hoc scaling vs. proper refit")
    print("=" * 70)

    merged, W = load_conus_graph_with_covariates()
    scale = compute_bym2_scale(W)
    X, colnames, prior_mu, prior_sigma, is_state_col = build_design_matrix(merged)
    state_col_names = [c for c, is_s in zip(colnames, is_state_col) if is_s]

    y_raw = merged["obdb_count"].to_numpy(dtype=float)
    y_corrected = merged["obdb_corrected_rounded"].to_numpy(dtype=float)
    capture_rate = merged["capture_rate"].to_numpy(dtype=float)
    log_exposure = np.log(merged["adults_21plus"].to_numpy(dtype=float))

    rng = np.random.default_rng(SEED)
    N = len(merged)
    perm = rng.permutation(N)
    n_test = int(round(N * TEST_FRACTION))
    test_idx = np.sort(perm[:n_test])
    train_idx = np.sort(perm[n_test:])
    print(f"Train: {len(train_idx)} counties, Test: {len(test_idx)} counties (same seed=42 split as the adopted model)")

    y_corrected_test = y_corrected[test_idx]

    # --- Fit 1: RAW counts, train fold only (regenerates the CV fold the
    # adopted model's own script already validated -- not saved from that run) --
    print("\nFitting RAW-count model on the train fold...")
    idata_raw = fit_bym2_nb_model(
        y_raw, log_exposure, W, scale, train_idx,
        X=X, prior_mu=prior_mu, prior_sigma=prior_sigma, is_state_col=is_state_col,
        draws=DRAWS, tune=TUNE, chains=CHAINS, target_accept=TARGET_ACCEPT, label="raw-cv",
    )
    alpha_raw, mu_raw, beta_raw = posterior_alpha_mu_beta(idata_raw)

    # --- Fit 2: CORRECTED counts, train fold only ---------------------------
    print("\nFitting CORRECTED-count model on the train fold...")
    idata_corrected = fit_bym2_nb_model(
        y_corrected, log_exposure, W, scale, train_idx,
        X=X, prior_mu=prior_mu, prior_sigma=prior_sigma, is_state_col=is_state_col,
        draws=DRAWS, tune=TUNE, chains=CHAINS, target_accept=TARGET_ACCEPT, label="corrected-cv",
    )
    alpha_corrected, mu_corrected, beta_corrected = posterior_alpha_mu_beta(idata_corrected)

    # --- Held-out comparison on the SAME target: corrected count, test fold -
    print("\n" + "=" * 70)
    print("HELD-OUT COMPARISON (both scored against the corrected count on the test fold)")
    print("=" * 70)

    # Post-hoc scaling: raw model's predicted mu divided by each held-out
    # county's own capture_rate, reusing the raw fit's own alpha (there is no
    # other alpha available for a model that was never fit on this scale).
    mu_raw_test_scaled = mu_raw[:, test_idx] / capture_rate[test_idx][None, :]
    ll_posthoc = nb_holdout_loglik_mc_prescaled(y_corrected_test, alpha_raw, mu_raw_test_scaled)
    print(f"Post-hoc scaled (raw fit / capture_rate)   mean held-out log-lik: {ll_posthoc:.4f} per county")

    # Direct: corrected model's own mu and alpha at the test indices.
    mu_corrected_test = mu_corrected[:, test_idx]
    ll_direct = nb_holdout_loglik_mc_prescaled(y_corrected_test, alpha_corrected, mu_corrected_test)
    print(f"Direct refit on corrected counts            mean held-out log-lik: {ll_direct:.4f} per county")

    diff = ll_direct - ll_posthoc
    print(f"\nDifference (direct - post-hoc): {diff:+.4f} per county "
          f"({'refit wins' if diff > 0 else 'post-hoc wins'})")

    pd.DataFrame({
        "method": ["Post-hoc scaled (raw fit / capture_rate)", "Direct refit on corrected counts"],
        "held_out_loglik_per_county": [ll_posthoc, ll_direct],
    }).to_csv(OUT_HOLDOUT_COMPARISON, index=False)
    print(f"Wrote {OUT_HOLDOUT_COMPARISON}")

    # --- State FE redundancy check ------------------------------------------
    print("\n" + "=" * 70)
    print("STATE FIXED-EFFECT REDUNDANCY CHECK")
    print("Does pre-correcting counts by a state-level capture rate make the")
    print("model's own state FE collapse toward each other (redundant), or")
    print("stay distinct (complementary, not redundant)?")
    print("=" * 70)

    beta_raw_median = np.median(beta_raw, axis=0)
    beta_corrected_median = np.median(beta_corrected, axis=0)

    state_names = [c.split("[T.")[-1].rstrip("]") for c in state_col_names]
    state_idx_in_beta = [colnames.index(c) for c in state_col_names]

    state_capture = merged.groupby("state_abbr")["capture_rate"].mean()
    fe_table = pd.DataFrame({
        "state_abbr": state_names,
        "mean_capture_rate": [state_capture.get(s, np.nan) for s in state_names],
        "raw_fe_median": beta_raw_median[state_idx_in_beta],
        "corrected_fe_median": beta_corrected_median[state_idx_in_beta],
    })
    fe_table["log_inv_capture_rate"] = -np.log(fe_table["mean_capture_rate"])
    fe_table["fe_diff"] = fe_table["corrected_fe_median"] - fe_table["raw_fe_median"]
    fe_table = fe_table.sort_values("fe_diff", ascending=False)
    fe_table.to_csv(OUT_STATE_FE_REDUNDANCY, index=False)
    print(f"Wrote {OUT_STATE_FE_REDUNDANCY}")

    rho_fe, p_fe = stats.pearsonr(fe_table["raw_fe_median"], fe_table["corrected_fe_median"])
    print(f"\nCorrelation between raw-fit and corrected-fit state FE (all 49 states): "
          f"r={rho_fe:.4f} (p={p_fe:.3g})")
    print(f"Raw state FE spread (sd across states):       {fe_table['raw_fe_median'].std():.4f}")
    print(f"Corrected state FE spread (sd across states): {fe_table['corrected_fe_median'].std():.4f}")

    rho_diff_vs_correction, p_diff = stats.pearsonr(fe_table["fe_diff"], fe_table["log_inv_capture_rate"])
    print(f"\nCorrelation between (corrected_fe - raw_fe) and each state's own "
          f"log(1/capture_rate) adjustment: r={rho_diff_vs_correction:.4f} (p={p_diff:.3g})")
    print("(A strong positive correlation here is expected and mechanical -- correcting a state's")
    print("counts upward by log(1/capture_rate) should shift that state's fitted intercept by about")
    print("the same amount, holding covariates/spatial fixed. The interesting number above is the")
    print("*spread* comparison: if state FE spread barely shrinks despite that shift, states still")
    print("differ from each other for reasons besides the capture-rate correction -- i.e. the")
    print("correction and state FE are not simply duplicating one another.)")

    print("\nBiggest movers (state FE, corrected - raw), top 10 each direction:")
    print(fe_table.head(10).to_string(index=False))
    print(fe_table.tail(10).to_string(index=False))

    # --- Recommendation -------------------------------------------------------
    print("\n" + "=" * 70)
    print("RECOMMENDATION")
    print("=" * 70)
    if diff > 0.01:
        print(f"Direct refit on corrected counts beats post-hoc scaling by {diff:.4f} log-lik/county.")
        print("This supports building a corrected-count production fit as a genuine improvement,")
        print("not just a post-hoc adjustment. Not run in this script -- rerun as a full production")
        print("fit (FINAL_DRAWS/TUNE/CHAINS/TARGET_ACCEPT settings) if you want to adopt it.")
    else:
        print(f"Direct refit does NOT clearly beat post-hoc scaling (diff={diff:+.4f} log-lik/county).")
        print("Given the added pipeline complexity (a second full model to maintain, rounding a")
        print("continuous correction to integer counts, wide pooled-extrapolation uncertainty for")
        print("28 states baked directly into the likelihood rather than shown as a separate caveat),")
        print("this does not clear the bar to replace the adopted raw-count model.")


if __name__ == "__main__":
    main()
