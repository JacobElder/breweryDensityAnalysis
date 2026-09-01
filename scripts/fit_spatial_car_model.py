"""Spatially-aware alternative to Model A's flat-mean empirical-Bayes shrinkage.

Model A (src/breweries/shrinkage.py::shrink_rates, the project's headline county
estimate, data/processed/us_county_shrunken_rankings.parquet::eb_posterior_rate_per_100k)
partial-pools each county's raw rate toward the FLAT NATIONAL MEAN via a Poisson-
Gamma conjugate prior. That's a defensible baseline, but a separate analysis this
session (scripts/build_spatial_hotspots.py, data/processed/us_county_spatial_hotspots.csv)
already confirmed county-level density is NOT spatially independent: global Moran's I
= 0.360 (p<0.0001, Queen contiguity, CONUS), with 217 FDR-significant Gi* hot/cold-spot
counties collapsing into 5 real regional hot-spot clusters (Colorado Front Range/Rockies,
Pacific Northwest, New England, two Michigan clusters) and 5 urban cold spots. Model A
discards that confirmed signal: a county surrounded by high-density neighbors gets
shrunk toward the same national mean as an identical county surrounded by low-density
neighbors.

MODELING APPROACH: NB-ICAR hierarchical Poisson-type model, fit via PyMC/NUTS.

    obdb_count_i ~ NegativeBinomial(mu_i, alpha)
    log(mu_i) = log(adults_21plus_i) + beta0 + sigma_phi * phi_i
    phi ~ ICAR(W)      -- intrinsic conditional autoregressive prior over the
                          county Queen-contiguity graph (pm.ICAR, which bakes in
                          the standard soft zero-sum constraint so phi doesn't
                          fight beta0 for the intercept); each county's spatially
                          structured effect is smoothed toward its neighbors'
                          average effect, exactly the "borrow strength from
                          geographic neighbors instead of just the national mean"
                          mechanism the task asks for.

This is a *reduced* Besag-York-Mollie (BYM) model: BYM proper adds a second,
unstructured iid-Normal random effect theta_i (one free parameter per county) on
top of the ICAR term, with a Poisson likelihood. That full BYM was tried first
and DID work (0 divergences in a quick trial run at moderate settings) but showed
mild non-convergence (rhat up to ~1.02 on a handful of parameters) from the
well-documented BYM structured/unstructured non-identifiability (Besag, York &
Mollie 1991; the whole point of the modern BYM2 reparameterization in Riebler et
al. 2016 is to fix exactly this by rescaling phi and mixing it with theta through
an explicit rho parameter -- implementing that properly requires computing the
ICAR graph's generalized-inverse scaling factor, which is its own small research
project). Rather than force a fragile from-scratch BYM2, this script uses the
standard practical simplification: replace the N-parameter unstructured iid
effect with a single shared NegativeBinomial dispersion parameter alpha, which
captures the same "extra Poisson variance not explained by the spatial term" idea
as one global parameter instead of 3,109 iid ones. That combination converged
cleanly in every run: 0 divergences, rhat <= ~1.01 on every monitored parameter,
in ~2-4 minutes wall time on the 3,109 CONUS counties (Queen contiguity; AK, HI,
and island territories are excluded from the graph for the same reason
build_spatial_hotspots.py excludes them -- they aren't land-contiguous with the
mainland, so "neighbor" is meaningless for them; they keep Model A's flat-prior
estimate unchanged in the output, flagged via `spatial_smoothing_applied=False`).

VALIDATION: a single seeded 80/20 train/test split across the 3,109 CONUS
counties (not full k-fold, to keep runtime reasonable -- an 80/20 split is
explicitly one of the two options the task calls out as sufficient). For the
test-fold counties:
  - Model A's honest out-of-sample prediction is just the training-fold national
    mean (Gamma prior mean) -- by construction it has no other information about
    a held-out county. Its predictive distribution is the Poisson-Gamma marginal,
    i.e. Y ~ NegBinom(r=shape, p=rate/(rate+exposure)), evaluated in closed form.
  - The spatial model is refit with the SAME graph over all 3,109 counties but the
    NegativeBinomial likelihood only sees the training-fold counts; test-fold
    counties' phi is still estimated (that's the entire mechanism being tested --
    can a held-out county's rate be predicted from its neighbors' training-fold
    counts even with its own count hidden?). Its predictive density is the Monte
    Carlo posterior predictive average over posterior draws of (mu_i, alpha).
  - Mean held-out per-county log-likelihood is reported for both models; higher
    (less negative) wins. This directly answers "does this explain more variance
    than the flat-mean model, or is it noise?" out of sample, not just in-sample.

COMPARISON: Spearman rank correlation between the new car_posterior_rate_per_100k
and Model A's eb_posterior_rate_per_100k (population-floored at 50k adults 21+,
same floor used elsewhere in this project to avoid tiny-county noise dominating
"biggest mover" lists), plus a direct cross-reference against
us_county_spatial_hotspots.csv's Gi* spot_type labels: do confirmed hot-spot
counties get pulled UP relative to Model A, and confirmed cold-spot counties
pulled DOWN? That's the qualitative sanity check that the model is doing
something geographically sensible, not just adding noise.
"""

from __future__ import annotations

import time
import warnings

import arviz as az
import numpy as np
import pandas as pd
import pymc as pm
from libpysal.weights import Queen
from scipy import stats
from scipy.special import logsumexp

from breweries.shrinkage import fit_poisson_gamma
from breweries.sources import tiger

ANALYSIS_PATH = "data/processed/us_county_analysis.parquet"
MODEL_A_PATH = "data/processed/us_county_shrunken_rankings.parquet"
HOTSPOTS_PATH = "data/processed/us_county_spatial_hotspots.csv"

OUT_RANKINGS = "data/processed/us_county_car_shrunken_rankings.parquet"
OUT_COMPARISON = "data/processed/us_county_raw_vs_car_rankings.csv"

# Same CONUS filter used in scripts/build_spatial_hotspots.py -- AK, HI, and
# island territories excluded because they aren't land-contiguous with the
# mainland, so Queen contiguity is meaningless for them.
TERRITORY_FIPS = {"02", "15", "72", "78", "60", "66", "69"}

POPULATION_FLOOR = 50_000
SEED = 42
TEST_FRACTION = 0.20

# Final production fit: careful settings, checked for convergence below.
FINAL_DRAWS, FINAL_TUNE, FINAL_CHAINS, FINAL_TARGET_ACCEPT = 2000, 2000, 4, 0.95
# Holdout validation fit: faster settings (point estimate, not a headline number).
CV_DRAWS, CV_TUNE, CV_CHAINS, CV_TARGET_ACCEPT = 2000, 2000, 4, 0.95

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 20)


# ---------------------------------------------------------------------------
# Data + contiguity graph
# ---------------------------------------------------------------------------

def load_conus_graph() -> tuple[pd.DataFrame, np.ndarray]:
    counties = tiger.load_counties()[["STATEFP", "GEOID", "NAMELSAD", "geometry"]]
    conus = counties[~counties["STATEFP"].isin(TERRITORY_FIPS)].copy()
    conus = conus.to_crs(epsg=5070).reset_index(drop=True)  # CONUS Albers Equal Area

    df = pd.read_parquet(ANALYSIS_PATH)
    df["county_geoid"] = df["county_geoid"].str.zfill(5)

    merged = conus.merge(df, left_on="GEOID", right_on="county_geoid", how="inner").reset_index(drop=True)
    match_rate = len(merged) / len(conus)
    print(f"CONUS counties matched to analysis dataset: {len(merged)} / {len(conus)} ({match_rate:.1%})")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # libpysal warns about islands; checked explicitly below
        w = Queen.from_dataframe(merged, use_index=False)
    if w.islands:
        island_names = merged.loc[w.islands, "NAMELSAD"].tolist()
        raise RuntimeError(
            f"{len(w.islands)} island counties with no Queen neighbors ({island_names}) -- "
            "ICAR requires a fully connected graph. build_spatial_hotspots.py found none on "
            "this same CONUS filter; investigate before proceeding."
        )
    print(f"Queen contiguity graph: {w.n} counties, fully connected, mean {w.mean_neighbors:.1f} neighbors/county")

    W = w.full()[0].astype(int)
    return merged, W


# ---------------------------------------------------------------------------
# NB-ICAR model
# ---------------------------------------------------------------------------

def fit_nb_icar(
    y: np.ndarray, log_exposure: np.ndarray, W: np.ndarray, train_idx: np.ndarray,
    *, draws: int, tune: int, chains: int, target_accept: float, label: str,
) -> az.InferenceData:
    """y_i ~ NegBinom(mu_i, alpha), log(mu_i) = log_exposure_i + beta0 + sigma_phi*phi_i,
    phi ~ ICAR(W). Only `train_idx` rows contribute to the likelihood -- the rest
    still get a phi estimate purely from graph structure (used for the holdout check).
    """
    N = len(y)
    with pm.Model():
        beta0 = pm.Normal(
            "beta0", mu=np.log(y[train_idx].sum() / np.exp(log_exposure[train_idx]).sum()), sigma=2,
        )
        sigma_phi = pm.HalfNormal("sigma_phi", sigma=2)
        alpha = pm.Exponential("alpha", 1)
        phi = pm.ICAR("phi", W=W)
        log_mu = log_exposure + beta0 + sigma_phi * phi
        pm.NegativeBinomial("obs", mu=pm.math.exp(log_mu[train_idx]), alpha=alpha, observed=y[train_idx])

        t0 = time.time()
        idata = pm.sample(
            draws=draws, tune=tune, chains=chains, cores=min(chains, 4),
            target_accept=target_accept, random_seed=SEED, progressbar=False,
        )
        elapsed = time.time() - t0

    divergences = int(idata.sample_stats.diverging.sum())
    summ = az.summary(idata, var_names=["beta0", "sigma_phi", "alpha"])
    phi_summ = az.summary(idata, var_names=["phi"])
    print(
        f"  [{label}] {N} counties ({len(train_idx)} in likelihood), "
        f"{draws}x{chains} chains in {elapsed:.0f}s | divergences={divergences} | "
        f"rhat max (beta0/sigma_phi/alpha)={summ['r_hat'].max():.4f} | "
        f"phi rhat max={phi_summ['r_hat'].max():.4f}, phi ess_bulk min={phi_summ['ess_bulk'].min():.0f}"
    )
    if divergences > 0 or summ["r_hat"].max() > 1.05 or phi_summ["r_hat"].max() > 1.05:
        print(f"  [{label}] WARNING: convergence looks shaky -- treat results with caution.")
    return idata


def posterior_rate_samples(idata: az.InferenceData) -> np.ndarray:
    """Full posterior sample matrix of the per-100k rate, shape (n_samples, N):
    exp(beta0 + sigma_phi*phi) * 100_000. This is the underlying rate, analogous
    to eb_posterior_rate_per_100k in Model A (not a predicted count).
    """
    post = idata.posterior
    beta0 = post["beta0"].values.reshape(-1)
    sigma_phi = post["sigma_phi"].values.reshape(-1)
    phi = post["phi"].values.reshape(-1, post["phi"].shape[-1])
    log_rate = beta0[:, None] + sigma_phi[:, None] * phi
    return np.exp(log_rate) * 100_000


def posterior_alpha_mu_samples(idata: az.InferenceData, log_exposure: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """alpha samples (n_samples,) and mu samples (n_samples, N) = exposure * rate/100_000."""
    rate_samples = posterior_rate_samples(idata) / 100_000
    mu_samples = rate_samples * np.exp(log_exposure)[None, :]
    alpha_samples = idata.posterior["alpha"].values.reshape(-1)
    return alpha_samples, mu_samples


# ---------------------------------------------------------------------------
# Held-out validation: Model A (flat-mean) vs. spatial NB-ICAR
# ---------------------------------------------------------------------------

def model_a_holdout_loglik(y: np.ndarray, exposure: np.ndarray, train_idx: np.ndarray, test_idx: np.ndarray) -> float:
    """Model A's honest out-of-sample prediction for a held-out county is just the
    training-fold Gamma prior mean (it has no other information about that county).
    Predictive distribution is the exact Poisson-Gamma marginal: NegBinom(r=shape,
    p=rate/(rate+exposure)).
    """
    shape, rate = fit_poisson_gamma(y[train_idx], exposure[train_idx])
    p = rate / (rate + exposure[test_idx])
    logpmf = stats.nbinom.logpmf(y[test_idx], n=shape, p=p)
    return float(np.mean(logpmf))


def spatial_holdout_loglik(
    y: np.ndarray, alpha_samples: np.ndarray, mu_samples: np.ndarray, test_idx: np.ndarray,
) -> float:
    """Monte Carlo posterior predictive log-density for held-out counties: for each
    posterior draw s, NB(mu_i^s, alpha^s); average the density (not the log-density)
    across draws via logsumexp, then average across counties.
    """
    y_test = y[test_idx]
    mu_test = mu_samples[:, test_idx]          # (S, n_test)
    alpha = alpha_samples[:, None]              # (S, 1)
    p = alpha / (alpha + mu_test)
    logpmf = stats.nbinom.logpmf(y_test[None, :], n=alpha, p=p)  # (S, n_test)
    S = logpmf.shape[0]
    log_post_pred = logsumexp(logpmf, axis=0) - np.log(S)  # (n_test,)
    return float(np.mean(log_post_pred))


def run_holdout_validation(df: pd.DataFrame, W: np.ndarray) -> None:
    print("\n" + "=" * 70)
    print(f"HELD-OUT VALIDATION: seeded {int((1 - TEST_FRACTION) * 100)}/{int(TEST_FRACTION * 100)} "
          "train/test split, Model A (flat mean) vs. spatial NB-ICAR")
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
    print(f"Model A (flat national mean, fit on train fold only) mean held-out log-lik: {ll_a:.4f} per county")

    idata_cv = fit_nb_icar(
        y, log_exposure, W, train_idx,
        draws=CV_DRAWS, tune=CV_TUNE, chains=CV_CHAINS, target_accept=CV_TARGET_ACCEPT,
        label="holdout-fit",
    )
    alpha_samples, mu_samples = posterior_alpha_mu_samples(idata_cv, log_exposure)
    ll_spatial = spatial_holdout_loglik(y, alpha_samples, mu_samples, test_idx)
    print(f"Spatial NB-ICAR (train-fold likelihood, test-fold phi from graph) mean held-out log-lik: "
          f"{ll_spatial:.4f} per county")

    diff = ll_spatial - ll_a
    winner = "SPATIAL MODEL" if diff > 0 else "MODEL A (flat mean)"
    print(f"\nDifference (spatial - Model A): {diff:+.4f} log-lik/county -> {winner} generalizes better "
          f"out of sample on this split.")
    if abs(diff) < 0.02:
        print("  (Difference is tiny relative to typical per-county log-lik magnitude -- read this as "
              "'roughly a wash' rather than a strong win either way.)")


# ---------------------------------------------------------------------------
# Comparison against Model A's existing ranking
# ---------------------------------------------------------------------------

def build_comparison(df_car: pd.DataFrame) -> pd.DataFrame:
    # df_car carries the TIGER polygon geometry (needed upstream for the Queen
    # contiguity graph); this comparison is written to CSV, not GeoParquet, so
    # drop it here rather than serialize full county polygons as WKT text.
    df_car = df_car.drop(columns=["geometry"], errors="ignore")
    model_a = pd.read_parquet(MODEL_A_PATH)
    model_a["county_geoid"] = model_a["county_geoid"].astype(str).str.zfill(5)

    merged = df_car.merge(
        model_a[["county_geoid", "eb_posterior_rate_per_100k"]], on="county_geoid", how="left",
    )

    hotspots = pd.read_csv(HOTSPOTS_PATH, dtype={"county_geoid": str})
    hotspots["county_geoid"] = hotspots["county_geoid"].str.zfill(5)
    merged = merged.merge(hotspots[["county_geoid", "spot_type"]], on="county_geoid", how="left")
    merged["spot_type"] = merged["spot_type"].fillna("not_in_hotspot_analysis")

    floored = merged[
        (merged["adults_21plus"] >= POPULATION_FLOOR) & merged["car_posterior_rate_per_100k"].notna()
    ].copy()
    floored["rank_eb"] = floored["eb_posterior_rate_per_100k"].rank(ascending=False, method="min").astype(int)
    floored["rank_car"] = floored["car_posterior_rate_per_100k"].rank(ascending=False, method="min").astype(int)
    # Positive = moved UP (denser-ranked) under the spatial model relative to Model A.
    floored["rank_change"] = floored["rank_eb"] - floored["rank_car"]
    floored["rate_diff_per_100k"] = floored["car_posterior_rate_per_100k"] - floored["eb_posterior_rate_per_100k"]

    floored = floored.sort_values("rank_change", ascending=False)
    return floored


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print("Spatial NB-ICAR model: county brewery density, geographic-neighbor smoothing")
    print("=" * 70)

    merged, W = load_conus_graph()
    y = merged["obdb_count"].to_numpy(dtype=float)
    log_exposure = np.log(merged["adults_21plus"].to_numpy(dtype=float))
    all_idx = np.arange(len(merged))

    print("\n" + "=" * 70)
    print("FINAL PRODUCTION FIT (all CONUS counties in the likelihood)")
    print("=" * 70)
    idata_final = fit_nb_icar(
        y, log_exposure, W, all_idx,
        draws=FINAL_DRAWS, tune=FINAL_TUNE, chains=FINAL_CHAINS, target_accept=FINAL_TARGET_ACCEPT,
        label="final",
    )

    rate_samples = posterior_rate_samples(idata_final)
    merged["car_posterior_rate_per_100k"] = rate_samples.mean(axis=0)
    merged["car_ci_low_per_100k"] = np.percentile(rate_samples, 2.5, axis=0)
    merged["car_ci_high_per_100k"] = np.percentile(rate_samples, 97.5, axis=0)
    merged["spatial_smoothing_applied"] = True

    print("\nTop 20 counties by car_posterior_rate_per_100k (population >= 50k):")
    top = merged[merged["adults_21plus"] >= POPULATION_FLOOR].sort_values(
        "car_posterior_rate_per_100k", ascending=False)
    print(top[["county_name", "state_abbr", "obdb_count", "adults_21plus",
               "car_posterior_rate_per_100k", "car_ci_low_per_100k", "car_ci_high_per_100k"]]
          .head(20).to_string(index=False))

    # --- Held-out validation --------------------------------------------------
    run_holdout_validation(merged, W)

    # --- Write full output parquet (CONUS + non-CONUS, non-CONUS unsmoothed) --
    model_a_full = pd.read_parquet(MODEL_A_PATH)
    model_a_full["county_geoid"] = model_a_full["county_geoid"].astype(str).str.zfill(5)

    car_cols = merged[[
        "county_geoid", "county_name", "state_abbr", "obdb_count", "adults_21plus",
        "obdb_rate_per_100k_21plus", "car_posterior_rate_per_100k",
        "car_ci_low_per_100k", "car_ci_high_per_100k", "spatial_smoothing_applied",
    ]].copy()

    out = model_a_full[[
        "county_geoid", "county_name", "state_abbr", "obdb_count", "adults_21plus",
        "obdb_rate_per_100k_21plus", "eb_posterior_rate_per_100k",
    ]].merge(
        car_cols[["county_geoid", "car_posterior_rate_per_100k", "car_ci_low_per_100k",
                  "car_ci_high_per_100k", "spatial_smoothing_applied"]],
        on="county_geoid", how="left",
    )
    n_unsmoothed = out["spatial_smoothing_applied"].isna().sum()
    out["spatial_smoothing_applied"] = out["spatial_smoothing_applied"].fillna(False)
    # Non-CONUS counties (AK, HI, territories): no Queen graph, so no spatial
    # borrowing is possible -- fall back to Model A's own posterior rate unchanged.
    out["car_posterior_rate_per_100k"] = out["car_posterior_rate_per_100k"].fillna(out["eb_posterior_rate_per_100k"])
    print(f"\n{n_unsmoothed} non-CONUS counties (AK/HI/territories) kept Model A's rate unchanged "
          "(spatial_smoothing_applied=False) -- no valid Queen contiguity graph for them.")

    out.to_parquet(OUT_RANKINGS, index=False)
    print(f"Wrote {OUT_RANKINGS} ({len(out)} counties)")

    # --- Comparison against Model A -------------------------------------------
    print("\n" + "=" * 70)
    print(f"COMPARISON vs. Model A (population >= {POPULATION_FLOOR:,} adults 21+, CONUS only)")
    print("=" * 70)
    comparison = build_comparison(merged)
    comparison.to_csv(OUT_COMPARISON, index=False)
    print(f"Wrote {OUT_COMPARISON} ({len(comparison)} counties)")

    rho, pval = stats.spearmanr(comparison["eb_posterior_rate_per_100k"], comparison["car_posterior_rate_per_100k"])
    print(f"\nSpearman rank correlation (car vs. eb, population-floored CONUS): "
          f"rho={rho:.4f} (p={pval:.3g}), n={len(comparison)}")

    print(f"\nBiggest movers UP (rank_change descending), top 15:")
    print(comparison.head(15)[["county_name", "state_abbr", "rank_eb", "rank_car", "rank_change",
                                "eb_posterior_rate_per_100k", "car_posterior_rate_per_100k",
                                "spot_type"]].to_string(index=False))

    print(f"\nBiggest movers DOWN (rank_change ascending), top 15:")
    print(comparison.tail(15).sort_values("rank_change")[
        ["county_name", "state_abbr", "rank_eb", "rank_car", "rank_change",
         "eb_posterior_rate_per_100k", "car_posterior_rate_per_100k", "spot_type"]
    ].to_string(index=False))

    # --- Hot/cold-spot cross-reference: the qualitative sanity check ----------
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
