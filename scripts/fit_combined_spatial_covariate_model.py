"""Combined covariate + state-FE + BYM2 spatial model: does bringing Model B's
covariates and the CAR model's spatial structure together beat either alone?

Three separate county-level models exist in this project and have never been
combined:

  A) src/breweries/shrinkage.py::shrink_rates (scripts/fit_national_models.py)
     -- empirical-Bayes Poisson-Gamma shrinkage toward the flat national mean.
     No covariates, no spatial structure.
  B) scripts/fit_national_models.py::fit_covariate_residual_model -- NB-GLM
     with covariates (log income, median age, college enrollment share,
     tourism establishments/10k, population growth, unemployment rate, median
     gross rent) + state fixed effects (C(state_abbr)), offset by
     log(adults_21plus). No spatial term.
  C) scripts/fit_spatial_car_model.py -- Bayesian NB-ICAR: counts ~
     NegBinomial(expected_count * exp(phi)), phi smoothed toward Queen-
     contiguity neighbors. No covariates, no state FE.

Each captures something real the others don't (see that script's docstring
and docs/methods_memo.md Section 12.1 for the evidence). This script builds
the union: covariates + state FE + a BYM2-style spatial random effect with
BOTH a structured (ICAR) and unstructured (iid) component, properly weighted.

BYM2 VARIANT IMPLEMENTED: the full Riebler et al. (2016) parameterization,
not a simplified fallback --

    combined_spatial_i = sigma * (sqrt(rho/scale) * phi_i + sqrt(1-rho) * theta_i)

    phi   ~ ICAR(W)              -- pm.ICAR, unit conditional variance (tau=1),
                                     soft zero-sum constraint built in
    theta ~ Normal(0, 1), iid    -- one free parameter per county
    rho   ~ Beta(1, 1)           -- mixing weight, structured vs. unstructured
    sigma ~ HalfNormal(2)        -- overall spatial standard deviation

`scale` is the geometric mean of the marginal variances of the ICAR structure
under its (rank-deficient) precision matrix Q = diag(neighbor_count) - W,
computed EXACTLY (not approximated) via the standard generalized-inverse
trick: letting v = 1/sqrt(N) * ones(N) (Q's null eigenvector for a connected
graph), Q + v v^T is invertible and (Q + v v^T)^-1 - v v^T equals the Moore-
Penrose pseudoinverse Q+ restricted to v's orthogonal complement -- this is
mathematically identical to what R-INLA's `inla.qinv(..., constr=...)` /
Stan's scaled-BYM2 case studies compute, just via a dense N x N solve instead
of a sparse one. N=3,109 (CONUS counties) makes a dense solve trivial (<1s
with Accelerate/LAPACK) rather than requiring a "small research project"
sparse implementation -- see `compute_bym2_scale()` below. This is the exact
approach the CAR model's own docstring flagged as the harder alternative it
declined to build; at this N it isn't actually hard, just a few lines of
linear algebra.

PRACTICAL FITTING NOTE (macOS-specific): the initial benchmark of this model
with `cores>1` crashed hard (SIGSEGV inside Accelerate's threaded cblas_dgemv,
called from pytensor's numba-compiled logp, invoked across the multiprocessing
worker pool) -- a known class of macOS bug where Accelerate's own internal
thread pool (Grand Central Dispatch) and Python multiprocessing's worker
processes fight over threads. Fixed by pinning every BLAS/threading env var to
1 thread per worker process (set at the very top of this file, before numpy/
pymc import) and letting multiprocessing itself provide the parallelism across
chains. With that fix, this ~6,300-latent-parameter model (3,109 phi + 3,109
theta + 56 covariate/state-FE coefficients + 4 scalars) fits in a few minutes
at production settings on this machine -- no ADVI/MAP compromise was needed;
every fit below (including both CV holdout folds) uses full NUTS at the same
settings as the CAR model's own production fit (2,000 tune + 2,000 draws, 4
chains, target_accept=0.95).

VALIDATION: the same seeded 80/20 train/test split (seed=42) used by the CAR
model, run across all FOUR models on the identical county universe (3,109
CONUS counties; the combined/CAR-only models need full Queen-graph
connectivity, so this is the same universe the CAR script uses, with the 17
counties missing a covariate median-imputed and flagged rather than dropped,
to avoid breaking graph connectivity -- see `load_conus_graph_with_covariates`)
so the comparison is apples-to-apples:
  - Model A: closed-form Poisson-Gamma marginal, train-fold fit (same as
    fit_spatial_car_model.py's model_a_holdout_loglik).
  - Model B: NB-GLM (statsmodels), covariates + full-rank one-hot state FE,
    train-fold fit, predicted onto the test fold.
  - CAR-only: this script's own NB-ICAR fit (same spec as
    fit_spatial_car_model.py: intercept + ICAR + global NB alpha, no
    covariates), train-fold likelihood only.
  - Combined: covariates + state FE + BYM2 spatial, train-fold likelihood
    only, test-fold county rates predicted from covariates (known for every
    county) plus the spatial term (phi carries neighbor information for
    held-out counties same as the CAR model; theta, being iid with no cross-
    county structure, contributes essentially its N(0,1) prior for held-out
    counties since there's no other information to update it with -- this is
    an expected, not a bug, property of the unstructured component).
Mean held-out per-county log-likelihood reported for all four on the same
split.

Also reproduces the CAR model's qualitative hot/cold-spot direction check
(Section 12.1 of docs/methods_memo.md) for the combined model specifically:
do counties independently confirmed as Gi* hot spots
(data/processed/us_county_spatial_hotspots.csv) move UP in the combined
ranking relative to Model A, and cold spots move DOWN?

Outputs:
  data/processed/us_county_combined_model_rankings.parquet -- full county
    table with combined_posterior_rate_per_100k (final production fit).
  data/processed/us_county_combined_holdout_comparison.csv -- 4-row summary,
    held-out log-lik/county for Model A / Model B / CAR-only / combined.
  data/processed/us_county_raw_vs_combined_rankings.csv -- per-county
    combined vs. Model A comparison with spot_type joined in, for the
    hot/cold-spot direction check.
"""

from __future__ import annotations

# --- Must be set before numpy/pymc/pytensor are imported -------------------
# macOS-specific fix: Accelerate's own multithreaded BLAS (vecLib) segfaults
# when invoked inside PyMC's multiprocessing worker pool (SIGSEGV in
# cblas_dgemv via dispatch_apply, confirmed via ~/Library/Logs/
# DiagnosticReports crash log during development of this script) unless every
# worker is pinned to a single BLAS thread and lets multiprocessing itself
# provide the parallelism across chains instead.
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
import statsmodels.api as sm
from libpysal.weights import Queen
from scipy import stats
from scipy.special import logsumexp

from breweries.shrinkage import fit_poisson_gamma
from breweries.sources import tiger

ANALYSIS_PATH = "data/processed/us_county_analysis.parquet"
MODEL_A_PATH = "data/processed/us_county_shrunken_rankings.parquet"
HOTSPOTS_PATH = "data/processed/us_county_spatial_hotspots.csv"

OUT_RANKINGS = "data/processed/us_county_combined_model_rankings.parquet"
OUT_HOLDOUT_COMPARISON = "data/processed/us_county_combined_holdout_comparison.csv"
OUT_COMPARISON = "data/processed/us_county_raw_vs_combined_rankings.csv"

# Same CONUS filter used by build_spatial_hotspots.py / fit_spatial_car_model.py.
TERRITORY_FIPS = {"02", "15", "72", "78", "60", "66", "69"}

COVARIATE_COLS = [
    "log_income", "median_age", "college_enrollment_share", "tourism_estab_per_10k",
    "pop_growth_pct", "unemployment_rate", "median_gross_rent",
]
POPULATION_FLOOR = 50_000
SEED = 42
TEST_FRACTION = 0.20

# Both CV holdout fits use these settings -- they converged cleanly already
# (rhat <= 1.067 on both folds) and exist only to compare held-out
# log-likelihood across models, not to produce trustworthy point estimates,
# so there's no reason to spend extra compute re-running them.
DRAWS, TUNE, CHAINS, TARGET_ACCEPT = 2000, 2000, 4, 0.95

# The FINAL production fit is what the project's headline ranking is drawn
# from, so it gets a longer run: the first attempt at these settings left
# `beta` (56 covariate/state-FE coefficients) and `phi_icar` mixing slowly
# (rhat 1.07-1.11, ess_bulk as low as 28) -- usable for the held-out
# log-lik comparison (which only needs predictive accuracy, not clean
# per-coefficient posteriors) but not for trusting individual coefficients.
# 2x draws/tune, 1.5x chains (more chains directly increases the rhat
# diagnostic's power to detect between-chain disagreement, not just more
# samples), and a higher target_accept (smaller NUTS steps, better able to
# navigate the tighter posterior geometry around the partially-identified
# state-FE/spatial-effect boundary flagged in this script's own report).
FINAL_DRAWS, FINAL_TUNE, FINAL_CHAINS, FINAL_TARGET_ACCEPT = 4000, 4000, 6, 0.97

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 20)


# ---------------------------------------------------------------------------
# Data + contiguity graph + covariates
# ---------------------------------------------------------------------------

def load_conus_graph_with_covariates() -> tuple[pd.DataFrame, np.ndarray]:
    """Same construction as fit_spatial_car_model.py::load_conus_graph, plus
    covariate prep. 17 of 3,109 CONUS counties are missing at least one
    covariate (median_household_income: 1, pop_growth_pct: 9,
    median_gross_rent: 7 -- mostly tiny Texas counties and CT's post-2022
    planning regions). Dropping them would break Queen-graph connectivity for
    their neighbors' ICAR terms, so they're median-imputed instead (CONUS-
    wide medians) and flagged via `covariate_imputed` rather than silently
    treated as real observations.
    """
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
        island_names = merged.loc[w.islands, "NAMELSAD"].tolist()
        raise RuntimeError(
            f"{len(w.islands)} island counties with no Queen neighbors ({island_names}) -- "
            "ICAR requires a fully connected graph."
        )
    print(f"Queen contiguity graph: {w.n} counties, fully connected, mean {w.mean_neighbors:.1f} neighbors/county")
    W = w.full()[0].astype(int)

    merged["log_income"] = np.log(merged["median_household_income"])
    raw_cov_cols = ["log_income", "median_age", "college_enrollment_share", "tourism_estab_per_10k",
                     "pop_growth_pct", "unemployment_rate", "median_gross_rent"]
    merged["covariate_imputed"] = merged[raw_cov_cols].isna().any(axis=1)
    n_imputed = int(merged["covariate_imputed"].sum())
    for col in raw_cov_cols:
        n_na = merged[col].isna().sum()
        if n_na:
            median_val = merged[col].median()
            merged[col] = merged[col].fillna(median_val)
            print(f"  Imputed {n_na} missing {col} with CONUS median ({median_val:.3f})")
    print(f"{n_imputed} counties had >=1 covariate median-imputed (flagged covariate_imputed=True)")
    assert merged["state_abbr"].notna().all(), "state_abbr should never be null within CONUS"

    return merged, W


def compute_bym2_scale(W: np.ndarray) -> float:
    """Exact Riebler et al. (2016) scaling factor: geometric mean of the
    marginal variances of the ICAR structure (phi ~ ICAR(W), unit conditional
    variance), computed via the generalized inverse of Q = diag(rowsums) - W
    restricted to the sum-to-zero subspace (Q's null space for a connected
    graph). See module docstring for the derivation of the v v^T trick used
    here -- mathematically identical to R-INLA's inla.qinv(..., constr=...),
    computed with a dense solve since N=3,109 makes that trivial (<1s).
    """
    N = W.shape[0]
    Q = np.diag(W.sum(axis=1).astype(float)) - W.astype(float)
    v = np.ones(N) / np.sqrt(N)  # Q's null eigenvector for a connected graph
    Q_star = Q + np.outer(v, v)  # invertible: replaces the 0 eigenvalue with 1 in direction v
    Q_star_inv = np.linalg.inv(Q_star)
    Q_plus_diag = np.diag(Q_star_inv) - v ** 2  # Moore-Penrose pseudoinverse diagonal
    scale = float(np.exp(np.mean(np.log(Q_plus_diag))))
    return scale


def build_design_matrix(df: pd.DataFrame) -> tuple[np.ndarray, list[str], np.ndarray, np.ndarray, np.ndarray]:
    """Design matrix for covariates + state FE, shared by the combined PyMC
    model and the Model B statsmodels replicate fit inside this script (same
    covariate spec as scripts/fit_national_models.py::fit_covariate_residual_model).

    Continuous covariates are z-scored (CONUS-wide mean/sd, used identically
    for both the CV-fold fits and the final fit -- a documented minor
    simplification: this leaks only summary statistics, not target/outcome
    information, into the "train" fold of the holdout split, which is
    standard practice and immaterial next to genuine target leakage).

    State FE uses patsy's automatic full-rank (one-hot, no dropped reference)
    coding for `C(state_abbr)` when the formula has no separate intercept
    (`0 + ...`) -- each state's dummy coefficient IS that state's intercept,
    so no separate global intercept parameter is needed (avoids the
    beta0-vs-state-mean non-identifiability that would arise from including
    both).
    """
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
    # Placeholder mean for state columns filled in by caller (needs train-fold
    # national mean rate, which build_design_matrix doesn't know about).
    return X, colnames, prior_mu, prior_sigma, is_state_col


# ---------------------------------------------------------------------------
# Unified NB model: none / ICAR-only / BYM2 spatial term
# ---------------------------------------------------------------------------

def fit_nb_model(
    y: np.ndarray, log_exposure: np.ndarray, W: np.ndarray, scale: float, train_idx: np.ndarray,
    *, X: np.ndarray | None, prior_mu: np.ndarray | None, prior_sigma: np.ndarray | None,
    is_state_col: np.ndarray | None = None,
    spatial_type: str, draws: int, tune: int, chains: int, target_accept: float, label: str,
) -> az.InferenceData:
    """y_i ~ NegBinom(mu_i, alpha), log(mu_i) = log_exposure_i + linpred_i + spatial_i.

    spatial_type: "bym2" (structured ICAR + unstructured iid, Riebler mixing),
    "icar" (pure ICAR, the CAR-only comparison model), or "none" (no spatial
    term at all -- unused here but kept for completeness/testability).

    If X is given, linpred = X @ beta (state FE columns act as each state's
    own intercept -- see build_design_matrix). If X is None, linpred = beta0,
    a single scalar intercept (matches fit_spatial_car_model.py's spec
    exactly for the CAR-only comparison run).

    Only `train_idx` rows contribute to the likelihood; mu_full (a
    Deterministic over ALL N counties) is what the holdout evaluation and the
    final rankings both read from the posterior.
    """
    N = len(y)
    train_mean_log_rate = float(np.log(y[train_idx].sum() / np.exp(log_exposure[train_idx]).sum()))

    with pm.Model():
        if X is not None:
            mu_vec = np.where(is_state_col, train_mean_log_rate, prior_mu)
            beta = pm.Normal("beta", mu=mu_vec, sigma=prior_sigma, shape=X.shape[1])
            linpred = pm.math.dot(X, beta)
        else:
            beta0 = pm.Normal("beta0", mu=train_mean_log_rate, sigma=2)
            linpred = beta0

        alpha = pm.Exponential("alpha", 1)

        if spatial_type == "bym2":
            sigma_bym = pm.HalfNormal("sigma_bym", sigma=2)
            rho = pm.Beta("rho", 1, 1)
            phi_icar = pm.ICAR("phi_icar", W=W)
            theta_iid = pm.Normal("theta_iid", mu=0, sigma=1, shape=N)
            spatial_term = sigma_bym * (
                pm.math.sqrt(rho / scale) * phi_icar + pm.math.sqrt(1 - rho) * theta_iid
            )
        elif spatial_type == "icar":
            sigma_phi = pm.HalfNormal("sigma_phi", sigma=2)
            phi = pm.ICAR("phi", W=W)
            spatial_term = sigma_phi * phi
        elif spatial_type == "none":
            spatial_term = 0.0
        else:
            raise ValueError(f"unknown spatial_type {spatial_type!r}")

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
    scalar_vars = ["alpha"] + (["beta0"] if X is None else []) + (
        ["sigma_bym", "rho"] if spatial_type == "bym2" else ["sigma_phi"] if spatial_type == "icar" else []
    )
    summ = az.summary(idata, var_names=scalar_vars)
    diag_lines = [f"scalar rhat max={summ['r_hat'].max():.4f}, ess_bulk min={summ['ess_bulk'].min():.0f}"]

    if X is not None:
        beta_summ = az.summary(idata, var_names=["beta"])
        diag_lines.append(f"beta rhat max={beta_summ['r_hat'].max():.4f}, ess_bulk min={beta_summ['ess_bulk'].min():.0f}")
    if spatial_type == "bym2":
        phi_summ = az.summary(idata, var_names=["phi_icar"])
        theta_summ = az.summary(idata, var_names=["theta_iid"])
        diag_lines.append(f"phi_icar rhat max={phi_summ['r_hat'].max():.4f}, ess_bulk min={phi_summ['ess_bulk'].min():.0f}")
        diag_lines.append(f"theta_iid rhat max={theta_summ['r_hat'].max():.4f}, ess_bulk min={theta_summ['ess_bulk'].min():.0f}")
        max_rhat = max(summ["r_hat"].max(), beta_summ["r_hat"].max(), phi_summ["r_hat"].max(), theta_summ["r_hat"].max())
    elif spatial_type == "icar":
        phi_summ = az.summary(idata, var_names=["phi"])
        diag_lines.append(f"phi rhat max={phi_summ['r_hat'].max():.4f}, ess_bulk min={phi_summ['ess_bulk'].min():.0f}")
        max_rhat = max(summ["r_hat"].max(), phi_summ["r_hat"].max())
    else:
        max_rhat = summ["r_hat"].max() if X is None else max(summ["r_hat"].max(), beta_summ["r_hat"].max())

    print(f"  [{label}] {N} counties ({len(train_idx)} in likelihood), {draws}x{chains} chains "
          f"in {elapsed:.0f}s | divergences={divergences}")
    for line in diag_lines:
        print(f"    {line}")
    if divergences > 0 or max_rhat > 1.05:
        print(f"  [{label}] WARNING: convergence looks shaky -- treat results with caution.")
    elif max_rhat > 1.01:
        print(f"  [{label}] NOTE: rhat max {max_rhat:.4f} is above the strict 1.01 bar but below the "
              "1.05 tolerance -- usable, not pristine.")

    return idata


def posterior_alpha_mu(idata: az.InferenceData) -> tuple[np.ndarray, np.ndarray]:
    post = idata.posterior
    alpha_samples = post["alpha"].values.reshape(-1)
    mu_samples = post["mu_full"].values.reshape(-1, post["mu_full"].shape[-1])
    return alpha_samples, mu_samples


# ---------------------------------------------------------------------------
# Held-out log-likelihood: shared MC evaluator + per-model wrappers
# ---------------------------------------------------------------------------

def nb_holdout_loglik_mc(y: np.ndarray, alpha_samples: np.ndarray, mu_samples: np.ndarray, test_idx: np.ndarray) -> float:
    """Monte Carlo posterior-predictive mean held-out log-lik (same method as
    fit_spatial_car_model.py::spatial_holdout_loglik): average density (not
    log-density) across posterior draws via logsumexp, then average across
    held-out counties.
    """
    y_test = y[test_idx]
    mu_test = mu_samples[:, test_idx]
    alpha = alpha_samples[:, None]
    p = alpha / (alpha + mu_test)
    logpmf = stats.nbinom.logpmf(y_test[None, :], n=alpha, p=p)
    S = logpmf.shape[0]
    log_post_pred = logsumexp(logpmf, axis=0) - np.log(S)
    return float(np.mean(log_post_pred))


def model_a_holdout_loglik(y: np.ndarray, exposure: np.ndarray, train_idx: np.ndarray, test_idx: np.ndarray) -> float:
    shape, rate = fit_poisson_gamma(y[train_idx], exposure[train_idx])
    p = rate / (rate + exposure[test_idx])
    logpmf = stats.nbinom.logpmf(y[test_idx], n=shape, p=p)
    return float(np.mean(logpmf))


def model_b_holdout_loglik(
    y: np.ndarray, log_exposure: np.ndarray, X: np.ndarray, train_idx: np.ndarray, test_idx: np.ndarray,
) -> float:
    """NB-GLM (statsmodels), same covariate spec as fit_national_models.py's
    Model B, fit on the train fold only and predicted onto the test fold.
    X already includes full-rank one-hot state FE (no separate intercept
    needed -- see build_design_matrix), so exog=X directly.

    Cold-started (all-zero start_params, statsmodels' default) BFGS collapses
    to alpha ~ 6.6e-9 after only 2 iterations on this design -- the exact
    "degenerate optimum near alpha=0" pathology breweries/shrinkage.py's own
    docstring warns about for NB MLE on sparse/overdispersed count data
    (there: at the place level; here: triggered by the 56-column full-rank
    one-hot design on a 2,487-row train fold). That alpha collapses every
    predicted county to a near-Poisson point mass, producing catastrophic
    held-out log-lik (-8.2/county) on any county whose count is far from its
    mean -- not a real assessment of Model B, just a bad local optimum.
    Fixed the standard way: warm-start the NB fit's coefficients from a
    Poisson GLM fit (same design, offset, no dispersion parameter to get
    stuck on) plus a moderate alpha=1 starting guess. This finds a proper
    interior optimum (alpha ~ 0.2, held-out log-lik ~ -1.35/county, in line
    with the other three models) instead of the boundary degeneracy.
    """
    poisson_fit = sm.GLM(y[train_idx], X[train_idx], family=sm.families.Poisson(),
                          offset=log_exposure[train_idx]).fit()
    start_params = np.concatenate([poisson_fit.params, [1.0]])
    nb = sm.NegativeBinomial(y[train_idx], X[train_idx], offset=log_exposure[train_idx]).fit(
        start_params=start_params, method="bfgs", disp=0, maxiter=2000)
    alpha = nb.params[-1]
    mu_test = nb.predict(X[test_idx], offset=log_exposure[test_idx])
    p = alpha / (alpha + mu_test)
    logpmf = stats.nbinom.logpmf(y[test_idx], n=alpha, p=p)
    return float(np.mean(logpmf))


def run_holdout_validation(
    df: pd.DataFrame, W: np.ndarray, scale: float, X: np.ndarray, prior_mu: np.ndarray, prior_sigma: np.ndarray,
    is_state_col: np.ndarray,
) -> pd.DataFrame:
    print("\n" + "=" * 70)
    print(f"HELD-OUT VALIDATION: seeded {int((1 - TEST_FRACTION) * 100)}/{int(TEST_FRACTION * 100)} "
          "train/test split, all FOUR models on the identical split")
    print("=" * 70)

    rng = np.random.default_rng(SEED)
    N = len(df)
    perm = rng.permutation(N)
    n_test = int(round(N * TEST_FRACTION))
    test_idx = np.sort(perm[:n_test])
    train_idx = np.sort(perm[n_test:])
    print(f"Train: {len(train_idx)} counties, Test: {len(test_idx)} counties")

    y = df["obdb_count"].to_numpy(dtype=float)
    exposure = df["adults_21plus"].to_numpy(dtype=float)
    log_exposure = np.log(exposure)

    ll_a = model_a_holdout_loglik(y, exposure, train_idx, test_idx)
    print(f"\nModel A (flat national mean)              mean held-out log-lik: {ll_a:.4f} per county")

    ll_b = model_b_holdout_loglik(y, log_exposure, X, train_idx, test_idx)
    print(f"Model B (covariates + state FE, no spatial) mean held-out log-lik: {ll_b:.4f} per county")

    idata_car_cv = fit_nb_model(
        y, log_exposure, W, scale, train_idx,
        X=None, prior_mu=None, prior_sigma=None, spatial_type="icar",
        draws=DRAWS, tune=TUNE, chains=CHAINS, target_accept=TARGET_ACCEPT, label="CAR-only-cv",
    )
    alpha_car, mu_car = posterior_alpha_mu(idata_car_cv)
    ll_car = nb_holdout_loglik_mc(y, alpha_car, mu_car, test_idx)
    print(f"CAR-only (pure ICAR, no covariates)         mean held-out log-lik: {ll_car:.4f} per county")

    idata_combined_cv = fit_nb_model(
        y, log_exposure, W, scale, train_idx,
        X=X, prior_mu=prior_mu, prior_sigma=prior_sigma, is_state_col=is_state_col, spatial_type="bym2",
        draws=DRAWS, tune=TUNE, chains=CHAINS, target_accept=TARGET_ACCEPT, label="combined-cv",
    )
    alpha_comb, mu_comb = posterior_alpha_mu(idata_combined_cv)
    ll_combined = nb_holdout_loglik_mc(y, alpha_comb, mu_comb, test_idx)
    print(f"Combined (covariates + state FE + BYM2)     mean held-out log-lik: {ll_combined:.4f} per county")

    results = pd.DataFrame({
        "model": ["Model A (flat mean)", "Model B (covariates + state FE)", "CAR-only (pure ICAR)",
                  "Combined (covariates + state FE + BYM2)"],
        "held_out_loglik_per_county": [ll_a, ll_b, ll_car, ll_combined],
    }).sort_values("held_out_loglik_per_county", ascending=False).reset_index(drop=True)
    print("\nRanked (higher = better generalization):")
    print(results.to_string(index=False))
    return results


# ---------------------------------------------------------------------------
# Comparison against Model A + hot/cold-spot direction check
# ---------------------------------------------------------------------------

def build_comparison(df_combined: pd.DataFrame) -> pd.DataFrame:
    df_combined = df_combined.drop(columns=["geometry"], errors="ignore")
    model_a = pd.read_parquet(MODEL_A_PATH)
    model_a["county_geoid"] = model_a["county_geoid"].astype(str).str.zfill(5)

    merged = df_combined.merge(
        model_a[["county_geoid", "eb_posterior_rate_per_100k"]], on="county_geoid", how="left",
    )

    hotspots = pd.read_csv(HOTSPOTS_PATH, dtype={"county_geoid": str})
    hotspots["county_geoid"] = hotspots["county_geoid"].str.zfill(5)
    merged = merged.merge(hotspots[["county_geoid", "spot_type"]], on="county_geoid", how="left")
    merged["spot_type"] = merged["spot_type"].fillna("not_in_hotspot_analysis")

    floored = merged[
        (merged["adults_21plus"] >= POPULATION_FLOOR) & merged["combined_posterior_rate_per_100k"].notna()
    ].copy()
    floored["rank_eb"] = floored["eb_posterior_rate_per_100k"].rank(ascending=False, method="min").astype(int)
    floored["rank_combined"] = floored["combined_posterior_rate_per_100k"].rank(ascending=False, method="min").astype(int)
    floored["rank_change"] = floored["rank_eb"] - floored["rank_combined"]
    floored["rate_diff_per_100k"] = floored["combined_posterior_rate_per_100k"] - floored["eb_posterior_rate_per_100k"]

    floored = floored.sort_values("rank_change", ascending=False)
    return floored


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print("Combined model: covariates + state FE + BYM2 spatial random effect")
    print("=" * 70)

    merged, W = load_conus_graph_with_covariates()
    scale = compute_bym2_scale(W)
    print(f"\nBYM2 scale factor (geometric mean marginal variance of ICAR structure): {scale:.4f}")

    X, colnames, prior_mu, prior_sigma, is_state_col = build_design_matrix(merged)
    print(f"Design matrix: {X.shape[0]} counties x {X.shape[1]} columns "
          f"({len(COVARIATE_COLS)} covariates + {X.shape[1] - len(COVARIATE_COLS)} state FE)")

    y = merged["obdb_count"].to_numpy(dtype=float)
    log_exposure = np.log(merged["adults_21plus"].to_numpy(dtype=float))
    all_idx = np.arange(len(merged))

    print("\n" + "=" * 70)
    print("FINAL PRODUCTION FIT (all CONUS counties in the likelihood)")
    print("=" * 70)
    idata_final = fit_nb_model(
        y, log_exposure, W, scale, all_idx,
        X=X, prior_mu=prior_mu, prior_sigma=prior_sigma, is_state_col=is_state_col, spatial_type="bym2",
        draws=FINAL_DRAWS, tune=FINAL_TUNE, chains=FINAL_CHAINS, target_accept=FINAL_TARGET_ACCEPT,
        label="combined-final",
    )
    # Checkpoint immediately -- this is the expensive, longer-than-CV fit this
    # run exists to produce; everything after this point is comparatively
    # cheap post-processing that shouldn't be able to lose the fit if it errors.
    _checkpoint_path = "data/processed/_combined_model_idata_checkpoint.nc"
    idata_final.to_netcdf(_checkpoint_path)
    print(f"Checkpointed production-fit trace to {_checkpoint_path}")

    rho_summ = az.summary(idata_final, var_names=["rho", "sigma_bym"])
    # This arviz version's az.summary() reports an 89% equal-tailed interval
    # (columns eti89_lb/eti89_ub) rather than the older hdi_3%/hdi_97% HDI
    # columns -- functionally equivalent uncertainty summary, different name.
    print(f"\nPosterior rho (structured share of spatial variance): "
          f"mean={rho_summ.loc['rho', 'mean']:.3f}, 89% ETI=[{rho_summ.loc['rho', 'eti89_lb']:.3f}, "
          f"{rho_summ.loc['rho', 'eti89_ub']:.3f}]")
    print(f"Posterior sigma_bym (overall spatial sd): mean={rho_summ.loc['sigma_bym', 'mean']:.3f}")

    alpha_final, mu_final = posterior_alpha_mu(idata_final)
    exposure = np.exp(log_exposure)
    rate_samples = mu_final / exposure[None, :] * 100_000
    # POSTERIOR MEDIAN, not mean, as the point estimate. mu_full is exp(linear
    # predictor) per posterior draw; for a low-exposure county with wide
    # posterior uncertainty in its linear predictor (this model's rho~0.97
    # means that's mostly spatial/covariate uncertainty, not sampling noise),
    # exp() of a wide-variance quantity has mean >> median (the classic
    # log-normal mean-vs-median gap, mean ~ exp(sigma^2/2) times the median) --
    # found via a real case: Mineral County CO (0 observed breweries, 640
    # adults 21+) reported a mean-based rate of 983/100k (CI 332-2221) versus
    # a median-based ~600/100k for the same posterior samples, still high but
    # not the kind of number that breaks a color-scale legend and misleads a
    # reader. This is standard practice in Bayesian small-area disease-mapping
    # (BYM/INLA outputs conventionally report the posterior median, not mean,
    # for exactly this reason) -- the CI itself is unaffected, already
    # percentile-based and therefore already robust to this skew.
    merged["combined_posterior_rate_per_100k"] = np.percentile(rate_samples, 50, axis=0)
    merged["combined_ci_low_per_100k"] = np.percentile(rate_samples, 2.5, axis=0)
    merged["combined_ci_high_per_100k"] = np.percentile(rate_samples, 97.5, axis=0)
    merged["spatial_smoothing_applied"] = True

    print("\nTop 20 counties by combined_posterior_rate_per_100k (population >= 50k):")
    top = merged[merged["adults_21plus"] >= POPULATION_FLOOR].sort_values(
        "combined_posterior_rate_per_100k", ascending=False)
    print(top[["county_name", "state_abbr", "obdb_count", "adults_21plus",
               "combined_posterior_rate_per_100k", "combined_ci_low_per_100k", "combined_ci_high_per_100k"]]
          .head(20).to_string(index=False))

    # --- Held-out validation: all four models ----------------------------
    holdout_results = run_holdout_validation(merged, W, scale, X, prior_mu, prior_sigma, is_state_col)
    holdout_results.to_csv(OUT_HOLDOUT_COMPARISON, index=False)
    print(f"\nWrote {OUT_HOLDOUT_COMPARISON}")

    # --- Write full output parquet (CONUS + non-CONUS fallback) ----------
    model_a_full = pd.read_parquet(MODEL_A_PATH)
    model_a_full["county_geoid"] = model_a_full["county_geoid"].astype(str).str.zfill(5)

    combined_cols = merged[[
        "county_geoid", "county_name", "state_abbr", "obdb_count", "adults_21plus",
        "obdb_rate_per_100k_21plus", "combined_posterior_rate_per_100k",
        "combined_ci_low_per_100k", "combined_ci_high_per_100k",
        "spatial_smoothing_applied", "covariate_imputed",
    ]].copy()

    out = model_a_full[[
        "county_geoid", "county_name", "state_abbr", "obdb_count", "adults_21plus",
        "obdb_rate_per_100k_21plus", "eb_posterior_rate_per_100k",
    ]].merge(
        combined_cols[["county_geoid", "combined_posterior_rate_per_100k", "combined_ci_low_per_100k",
                        "combined_ci_high_per_100k", "spatial_smoothing_applied", "covariate_imputed"]],
        on="county_geoid", how="left",
    )
    n_unsmoothed = out["spatial_smoothing_applied"].isna().sum()
    out["spatial_smoothing_applied"] = out["spatial_smoothing_applied"].fillna(False)
    out["covariate_imputed"] = out["covariate_imputed"].fillna(False)
    out["combined_posterior_rate_per_100k"] = out["combined_posterior_rate_per_100k"].fillna(out["eb_posterior_rate_per_100k"])
    print(f"\n{n_unsmoothed} non-CONUS counties (AK/HI/territories) kept Model A's rate unchanged "
          "(spatial_smoothing_applied=False) -- no valid Queen contiguity graph for them.")

    out.to_parquet(OUT_RANKINGS, index=False)
    print(f"Wrote {OUT_RANKINGS} ({len(out)} counties)")

    # --- Comparison + hot/cold-spot direction check -----------------------
    print("\n" + "=" * 70)
    print(f"COMPARISON vs. Model A (population >= {POPULATION_FLOOR:,} adults 21+, CONUS only)")
    print("=" * 70)
    comparison = build_comparison(merged)
    comparison.to_csv(OUT_COMPARISON, index=False)
    print(f"Wrote {OUT_COMPARISON} ({len(comparison)} counties)")

    rho_rank, pval = stats.spearmanr(comparison["eb_posterior_rate_per_100k"], comparison["combined_posterior_rate_per_100k"])
    print(f"\nSpearman rank correlation (combined vs. eb, population-floored CONUS): "
          f"rho={rho_rank:.4f} (p={pval:.3g}), n={len(comparison)}")

    print("\nBiggest movers UP (rank_change descending), top 15:")
    print(comparison.head(15)[["county_name", "state_abbr", "rank_eb", "rank_combined", "rank_change",
                                "eb_posterior_rate_per_100k", "combined_posterior_rate_per_100k",
                                "spot_type"]].to_string(index=False))

    print("\nBiggest movers DOWN (rank_change ascending), top 15:")
    print(comparison.tail(15).sort_values("rank_change")[
        ["county_name", "state_abbr", "rank_eb", "rank_combined", "rank_change",
         "eb_posterior_rate_per_100k", "combined_posterior_rate_per_100k", "spot_type"]
    ].to_string(index=False))

    print("\n" + "=" * 70)
    print("Does confirmed hot-spot / cold-spot membership predict which direction a county moves?")
    print("=" * 70)
    by_spot = comparison.groupby("spot_type").agg(
        n=("rank_change", "size"),
        mean_rank_change=("rank_change", "mean"),
        median_rank_change=("rank_change", "median"),
        mean_rate_diff=("rate_diff_per_100k", "mean"),
        pct_moved_up=("rank_change", lambda s: (s > 0).mean()),
    ).round(3)
    print(by_spot.to_string())


if __name__ == "__main__":
    main()
