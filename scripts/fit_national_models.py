"""Fit the two model deliverables on the national county dataset:

A) Empirical Bayes Poisson-Gamma shrinkage of the raw rate (no covariates) —
   partial pooling toward the national mean, via the NB-GLM/Poisson-Gamma
   conjugate-mixture equivalence (the "cheap" option the handoff allows).
B) Negative-binomial GLM with covariates + state fixed effects, offset by
   log(adults_21plus) — ranked by residual (observed/expected), i.e. the
   "more breweries than expected after conditioning on tourism, age, income,
   population growth, unemployment, median rent, and state regulatory regime"
   list.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from breweries.shrinkage import fit_poisson_gamma, shrink_rates

pd.set_option("display.width", 160)
pd.set_option("display.max_colwidth", 30)

POPULATION_FLOOR = 50_000


def fit_empirical_bayes_shrinkage(df: pd.DataFrame) -> pd.DataFrame:
    # Method-of-moments Poisson-Gamma (see breweries/shrinkage.py) — used
    # consistently across county/CBSA/place levels rather than NB MLE, which was
    # found to be numerically unstable at the place level.
    df_shrunk = shrink_rates(df, "obdb_count", "adults_21plus")
    shape, rate = fit_poisson_gamma(df["obdb_count"].values, df["adults_21plus"].values)
    print(f"National mean rate (prior): {shape / rate * 100_000:.2f} per 100k adults 21+")
    print(f"Gamma shape={shape:.3f}, rate={rate:.2e}")
    return df_shrunk


def fit_covariate_residual_model(df: pd.DataFrame) -> tuple[pd.DataFrame, object]:
    df = df.copy()
    df["log_offset"] = np.log(df["adults_21plus"])
    df["log_income"] = np.log(df["median_household_income"])
    model_df = df.dropna(subset=["log_income", "median_age", "college_enrollment_share",
                                  "tourism_estab_per_10k", "pop_growth_pct", "unemployment_rate",
                                  "median_gross_rent", "state_abbr"]).copy()

    formula = ("obdb_count ~ log_income + median_age + college_enrollment_share "
               "+ tourism_estab_per_10k + pop_growth_pct + unemployment_rate "
               "+ median_gross_rent + C(state_abbr)")
    nb = smf.negativebinomial(formula, data=model_df, offset=model_df["log_offset"]).fit(
        method="bfgs", disp=0, maxiter=500)

    # statsmodels' discrete NegativeBinomial.predict() needs the offset passed
    # explicitly even when the model was fit with one — otherwise it silently
    # predicts as if offset=0, which for a log(adults_21plus) offset means
    # predicting as if every county had exactly 1 adult.
    model_df["expected_count"] = nb.predict(model_df, offset=model_df["log_offset"])
    model_df["residual_ratio"] = model_df["obdb_count"] / model_df["expected_count"]

    # The raw residual ratio has the same small-count instability the handoff warns
    # about for raw rates (a county "expected" 0.3 breweries that has 2 looks like a
    # huge outlier off pure noise). Apply the same NB-Gamma partial-pooling shrinkage
    # used in Model A, but centered on each county's own covariate-based expectation
    # rather than the flat national mean, using the alpha this model just estimated.
    alpha_nb = nb.params["alpha"]
    shape = 1 / alpha_nb
    rate = 1 / (alpha_nb * model_df["expected_count"])
    model_df["shrunken_residual_ratio"] = (shape + model_df["obdb_count"]) / (rate + 1) / model_df["expected_count"]

    return model_df, nb


def main() -> None:
    df = pd.read_parquet("data/processed/us_county_analysis.parquet")

    print("=" * 70)
    print("MODEL A: Empirical Bayes Poisson-Gamma shrinkage (no covariates)")
    print("=" * 70)
    df_eb = fit_empirical_bayes_shrinkage(df)

    print("\nTop 20 by shrunken posterior rate (population >= 50k):")
    top_eb = df_eb[df_eb["adults_21plus"] >= POPULATION_FLOOR].sort_values(
        "eb_posterior_rate_per_100k", ascending=False)
    print(top_eb[["county_name", "state_abbr", "obdb_count", "adults_21plus",
                  "obdb_rate_per_100k_21plus", "eb_posterior_rate_per_100k",
                  "eb_ci_low_per_100k", "eb_ci_high_per_100k"]].head(20).to_string(index=False))

    df_eb.to_parquet("data/processed/us_county_shrunken_rankings.parquet", index=False)
    print("\nWrote data/processed/us_county_shrunken_rankings.parquet")

    print("\n" + "=" * 70)
    print("MODEL B: NB-GLM with covariates + state FE, ranked by residual")
    print("=" * 70)
    model_df, nb_fit = fit_covariate_residual_model(df)

    print(nb_fit.summary().tables[0])
    print("\nCovariate coefficients (excluding state FE):")
    coef_table = nb_fit.summary2().tables[1]
    non_state = coef_table[~coef_table.index.str.contains("state_abbr")]
    print(non_state)

    print("\nTop 20 by SHRUNKEN residual (partial pooling toward each county's own "
          "expectation), population >= 50k — 'more breweries than expected':")
    top_resid = model_df[model_df["adults_21plus"] >= POPULATION_FLOOR].sort_values(
        "shrunken_residual_ratio", ascending=False)
    print(top_resid[["county_name", "state_abbr", "obdb_count", "expected_count",
                     "residual_ratio", "shrunken_residual_ratio"]].head(20).to_string(index=False))

    print("\n(Unshrunk raw residual_ratio top 10, for comparison — note how much noisier:)")
    top_raw = model_df[model_df["adults_21plus"] >= POPULATION_FLOOR].sort_values(
        "residual_ratio", ascending=False)
    print(top_raw[["county_name", "state_abbr", "obdb_count", "expected_count",
                   "residual_ratio"]].head(10).to_string(index=False))

    model_df.to_parquet("data/processed/us_county_residual_rankings.parquet", index=False)
    print("\nWrote data/processed/us_county_residual_rankings.parquet")


if __name__ == "__main__":
    main()
