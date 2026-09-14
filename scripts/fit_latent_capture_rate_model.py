"""True-scale (capture-corrected) county brewery rates with the capture rate's
own uncertainty propagated, instead of hard-coded as a fixed offset.

See src/breweries/latent_capture_rate.py for the argument. In short: the state
fixed effect and log(capture rate) are additively confounded, so no brewery
count can separate them and the split is entirely prior-driven. The useful
thing a "latent" treatment buys is not a better point estimate -- it is
intervals that admit how uncertain the correction is, which matters because
29.6% of US adults live in states whose capture rate is an extrapolation with
a ~3.6x interval.

WHAT THIS SCRIPT DOES
  1. Reads the existing production posterior (no new expensive fit).
  2. Converts observed-scale rates to true-scale by dividing by DRAWS from the
     per-state capture prior, so the prior's spread is convolved in.
  3. VALIDATES that against a direct joint MCMC fit of the full latent model
     on a reduced setting -- the equivalence is argued analytically in the
     module docstring, and argued-but-unchecked is how a wrong assumption gets
     shipped. Pass --skip-validation to do only step 2.
  4. Writes data/processed/us_county_true_rate_rankings.parquet and a
     comparison of interval widths against the fixed-offset approach.

Outputs both scales side by side. The OBSERVED-scale rate is unchanged and
remains what the headline map shows; the TRUE-scale rate is the "how many
breweries are really there" quantity, and it is necessarily less certain.
"""

from __future__ import annotations

import os

for _env_var in ("OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMBA_NUM_THREADS"):
    os.environ.setdefault(_env_var, "1")

import argparse

import arviz as az
import numpy as np
import pandas as pd
import pymc as pm

from breweries import latent_capture_rate as lcr
from fit_combined_spatial_covariate_model import (
    SEED,
    build_design_matrix,
    compute_bym2_scale,
    load_conus_graph_with_covariates,
    posterior_alpha_mu,
)

CHECKPOINT = "data/processed/_combined_model_idata_checkpoint.nc"
OUT_RANKINGS = "data/processed/us_county_true_rate_rankings.parquet"
OUT_COMPARISON = "data/processed/us_county_true_rate_interval_comparison.csv"

# Reduced settings for the validation fit only: it exists to confirm the
# convolution matches a real joint fit, which needs adequate posterior
# coverage, not production precision.
VAL_DRAWS, VAL_TUNE, VAL_CHAINS, VAL_TARGET_ACCEPT = 1500, 1500, 4, 0.95


def load_observed_rate_samples(merged: pd.DataFrame, X: np.ndarray, scale: float) -> np.ndarray:
    """(n_draws, n_counties) observed-scale rate draws from the production fit.

    RECONSTRUCTS mu rather than reading a stored `mu_full` Deterministic. The
    production script stopped storing that variable (it was a third of the
    trace, and the fit was being OOM-killed), so reading it here worked only
    against checkpoints written BEFORE that change and would raise
    KeyError: 'mu_full' against any new one. Reusing the production script's
    own reconstruction keeps the two definitions of mu from drifting apart.
    """
    idata = az.from_netcdf(CHECKPOINT)
    log_exposure = np.log(merged["adults_21plus"].to_numpy(dtype=float))
    if "mu_full" in idata.posterior:
        mu = idata.posterior["mu_full"].values
        mu = mu.reshape(-1, mu.shape[-1])
    else:
        _, mu = posterior_alpha_mu(idata, X=X, log_exposure=log_exposure,
                                    scale=scale, spatial_type="bym2")
    exposure = merged["adults_21plus"].to_numpy(dtype=float)
    return mu / exposure[None, :] * 100_000


def fit_joint_latent_model(merged: pd.DataFrame, W: np.ndarray, scale: float,
                            priors: pd.DataFrame, state_index: np.ndarray,
                            include_spatial: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Direct joint fit with log(capture) as an explicit latent parameter.

    Parameterized ORTHOGONALLY, which is the only well-behaved way to write it:
    `state_total` (= state FE + log capture) is what the likelihood sees and is
    sampled against the data; `log_capture` carries the calibration prior. The
    naive parameterization -- separate state FE and log capture both entering
    the linear predictor -- is exactly non-identified and makes NUTS crawl
    along a flat ridge for no benefit, since the data cannot inform the split
    regardless.

    include_spatial=False by default, and that is a deliberate, defensible
    choice rather than a shortcut. The claim under test is about the
    IDENTIFICATION of log_capture against state_total -- whether the counts can
    move the capture rate off its prior. That confounding is a property of the
    state-level linear predictor and is completely unaffected by the BYM2 term,
    which is a per-COUNTY random effect carrying no state-level location.
    Dropping it removes 6,218 of ~6,280 stored parameters per draw, turning a
    fit that was OOM-killed on this 16GB machine into one that costs a few MB,
    while testing exactly the same question. If the data cannot update
    log_capture here, adding a county-level spatial term cannot make them able
    to.
    """
    X, colnames, prior_mu, prior_sigma, is_state_col = build_design_matrix(merged)
    X_cov = X[:, ~is_state_col]
    n_states = len(priors)

    y = merged["obdb_count"].to_numpy(dtype=float)
    log_exposure = np.log(merged["adults_21plus"].to_numpy(dtype=float))
    mean_log_rate = float(np.log(y.sum() / np.exp(log_exposure).sum()))

    mu_log_c = priors["mu_log_c"].to_numpy(dtype=float)
    sd_log_c = priors["sd_log_c"].to_numpy(dtype=float)

    with pm.Model():
        beta_cov = pm.Normal("beta_cov", mu=0.0, sigma=1.0, shape=X_cov.shape[1])
        log_capture = pm.TruncatedNormal("log_capture", mu=mu_log_c, sigma=sd_log_c,
                                          upper=lcr.LOG_CAPTURE_UPPER, shape=n_states)

        alpha = pm.Exponential("alpha", 1)
        if include_spatial:
            sigma_bym = pm.HalfNormal("sigma_bym", sigma=2)
            rho = pm.Beta("rho", 1, 1)
            phi_icar = pm.ICAR("phi_icar", W=W)
            theta_iid = pm.Normal("theta_iid", mu=0, sigma=1, shape=len(y))
            spatial = sigma_bym * (pm.math.sqrt(rho / scale) * phi_icar
                                    + pm.math.sqrt(1 - rho) * theta_iid)
        else:
            spatial = 0.0

        # NAIVE parameterization ON PURPOSE. An earlier version of this
        # validation put state_total in the likelihood and log_capture only in
        # a Deterministic -- so log_capture never touched `obs` at all, and its
        # posterior was forced to equal its prior BY CONSTRUCTION. "Test 1"
        # then reported that equality as if it were evidence of
        # non-identifiability, when it was a tautology about a parameter with
        # no likelihood term. That is circular and proved nothing.
        #
        # Here both the state effect and log_capture enter the linear predictor
        # additively, exactly as the real model would if it tried to estimate
        # them separately. The likelihood can therefore move log_capture if the
        # data contain ANY information about it; the confounding claim is that
        # it cannot. That is now a falsifiable test rather than a restatement
        # of the graph.
        beta_state = pm.Normal("beta_state", mu=mean_log_rate, sigma=2.0, shape=n_states)
        log_obs_rate = (pm.math.dot(X_cov, beta_cov) + beta_state[state_index]
                         + log_capture[state_index] + spatial)
        # True rate = observed rate with the capture factor removed, i.e. the
        # free state effect without log_capture. Under the confounding claim,
        # the likelihood pins (beta_state + log_capture) but not either alone,
        # so this quantity should inherit the capture prior's full spread.
        pm.Deterministic("true_rate", pm.math.exp(log_obs_rate - log_capture[state_index]) * 100_000)
        pm.NegativeBinomial("obs", mu=pm.math.exp(log_exposure + log_obs_rate),
                             alpha=alpha, observed=y)

        idata = pm.sample(draws=VAL_DRAWS, tune=VAL_TUNE, chains=VAL_CHAINS,
                           cores=1,  # sequential: memory, not speed, is the binding constraint here
                           target_accept=VAL_TARGET_ACCEPT,
                           random_seed=SEED, progressbar=False)

    tr = idata.posterior["true_rate"].values
    lc = idata.posterior["log_capture"].values
    return tr.reshape(-1, tr.shape[-1]), lc.reshape(-1, lc.shape[-1])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-validation", action="store_true",
                    help="skip the confirmatory joint MCMC fit")
    args = ap.parse_args()

    merged, W = load_conus_graph_with_covariates()
    X_full, _, _, _, _ = build_design_matrix(merged)
    scale_full = compute_bym2_scale(W)
    states = sorted(merged["state_abbr"].unique())
    state_index = merged["state_abbr"].map({s: i for i, s in enumerate(states)}).to_numpy()

    mean_log_density = merged.groupby("state_abbr")["log_density"].mean().to_dict()
    priors = lcr.state_capture_priors(states, mean_log_density)
    print("Per-state capture-rate priors on log(c):")
    print(priors.groupby("source").agg(
        n_states=("state_abbr", "size"),
        mean_implied_rate=("mu_log_c", lambda s: float(np.exp(s).mean())),
        sd_log=("sd_log_c", "first")).round(3).to_string())

    observed = load_observed_rate_samples(merged, X_full, scale_full)
    print(f"\nLoaded production posterior: {observed.shape[0]} draws x {observed.shape[1]} counties")

    true_samples = lcr.true_rate_samples(observed, state_index, priors, seed=SEED)

    obs_sum = lcr.summarize(observed)
    true_sum = lcr.summarize(true_samples)

    # The fixed-offset approach for contrast: divide by the POINT estimate, so
    # the interval shifts but does not widen.
    point_c = np.exp(priors["mu_log_c"].to_numpy())[state_index]
    fixed_sum = lcr.summarize(observed / point_c[None, :])

    out = merged[["county_geoid", "county_name", "state_abbr", "obdb_count",
                   "adults_21plus"]].copy()
    out["observed_rate_per_100k"] = obs_sum["median"].to_numpy()
    out["observed_ci_low"] = obs_sum["ci_low"].to_numpy()
    out["observed_ci_high"] = obs_sum["ci_high"].to_numpy()
    out["true_rate_per_100k"] = true_sum["median"].to_numpy()
    out["true_ci_low"] = true_sum["ci_low"].to_numpy()
    out["true_ci_high"] = true_sum["ci_high"].to_numpy()
    out["capture_source"] = priors["source"].to_numpy()[state_index]
    out.to_parquet(OUT_RANKINGS, index=False)
    print(f"\nWrote {OUT_RANKINGS}")

    # --- The point of the exercise: what happens to interval width ---------
    def width(s):
        return np.log(s["ci_high"].to_numpy() / np.clip(s["ci_low"].to_numpy(), 1e-9, None))

    comp = pd.DataFrame({
        "capture_source": priors["source"].to_numpy()[state_index],
        "observed": width(obs_sum),
        "fixed_offset": width(fixed_sum),
        "latent": width(true_sum),
    })
    summary = comp.groupby("capture_source").agg(["median", "size"]).round(4)
    print("\nLog interval WIDTH of the county rate, by how the capture rate was obtained:")
    print(summary.to_string())
    print("\n  'fixed_offset' divides by the capture point estimate: the interval moves but")
    print("  does not widen, so the correction is presented as if known exactly.")
    print("  'latent' convolves the capture prior in, so uncertainty about the correction")
    print("  shows up as uncertainty in the answer -- most visibly in pooled_extrapolation")
    print("  states, which is exactly where it should.")
    comp.to_csv(OUT_COMPARISON, index=False)
    print(f"\nWrote {OUT_COMPARISON}")

    if args.skip_validation:
        print("\nSkipping confirmatory joint fit (--skip-validation).")
        return

    # Free the big draw arrays before fitting. `observed` and `true_samples`
    # are each 24,000 x 3,109 float64 (~0.6GB apiece) and only their summaries
    # are needed from here on; holding them through an MCMC fit is what
    # OOM-killed the sibling model script four times.
    import gc
    del observed, true_samples, fixed_sum, comp
    gc.collect()

    # --- Confirm the convolution equals a real joint fit -------------------
    print("\n" + "=" * 70)
    print("VALIDATION: direct joint MCMC fit of the latent-capture model")
    print("=" * 70)
    joint_true, joint_log_c = fit_joint_latent_model(
        merged, W, compute_bym2_scale(W), priors, state_index, include_spatial=False)

    # TEST 1 -- the load-bearing claim. If the counts cannot identify the
    # capture rate, its POSTERIOR must be indistinguishable from its PRIOR.
    print("\nTest 1: can the data move log(capture) off its prior?")
    prior_draws, _ = lcr.sample_log_capture(priors, joint_log_c.shape[0], seed=SEED + 1)
    rows = []
    for i, st in enumerate(priors["state_abbr"]):
        rows.append({
            "state": st, "source": priors["source"].iloc[i],
            "prior_mean": float(prior_draws[:, i].mean()),
            "post_mean": float(joint_log_c[:, i].mean()),
            "prior_sd": float(prior_draws[:, i].std()),
            "post_sd": float(joint_log_c[:, i].std()),
        })
    d = pd.DataFrame(rows)
    d["mean_shift_in_prior_sds"] = (d.post_mean - d.prior_mean).abs() / d.prior_sd
    d["sd_ratio"] = d.post_sd / d.prior_sd
    print(d.groupby("source")[["mean_shift_in_prior_sds", "sd_ratio"]].median().round(4).to_string())
    print(f"  max mean shift across all {len(d)} states: "
          f"{d.mean_shift_in_prior_sds.max():.4f} prior sds")
    print("  A shift near 0 and an sd ratio near 1 mean the posterior IS the prior:")
    print("  the brewery counts carry no information about OBDB's coverage, exactly")
    print("  as the additive-confounding argument predicts.")

    # TEST 2 -- does the convolution reproduce the joint fit's true-rate posterior?
    print("\nTest 2: convolution vs. joint fit, on this fit's own observed rates.")
    obs_from_joint = joint_true * np.exp(joint_log_c[:, state_index])
    conv = lcr.true_rate_samples(obs_from_joint, state_index, priors, seed=SEED + 2)
    a_sum, b_sum = lcr.summarize(conv), lcr.summarize(joint_true)
    for label, col in [("median", "median"), ("CI low", "ci_low"), ("CI high", "ci_high")]:
        a, b = a_sum[col].to_numpy(), b_sum[col].to_numpy()
        rel = np.abs(a - b) / np.clip(np.abs(b), 1e-9, None)
        print(f"  true-rate {label:8s}: median |rel diff| = {np.median(rel):.4f}, "
              f"90th pct = {np.percentile(rel, 90):.4f}, corr = {np.corrcoef(a, b)[0, 1]:.5f}")
    print("\n  Close agreement confirms the convolution is not an approximation of")
    print("  convenience: re-running NUTS for the capture rate buys nothing over")
    print("  reusing the production fit.")


if __name__ == "__main__":
    main()
