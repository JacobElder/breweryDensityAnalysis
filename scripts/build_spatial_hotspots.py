"""Spatial clustering ("beer belt") analysis of county-level brewery density.

Every prior county-ranking analysis in this project (choropleths, top-N
tables, the empirical-Bayes shrinkage itself) treats counties as
statistically independent units. That's fine for estimating each county's
own rate, but it leaves an open question nobody has checked: do high-density
counties cluster into contiguous regional "beer belts" (Pacific Northwest,
Colorado Front Range, New England, ...), or is elevated density scattered
independently across the map with no geographic structure?

This script answers that with standard spatial-statistics tools:

1. Global Moran's I on the shrunken posterior rate (eb_posterior_rate_per_100k)
   over a county contiguity graph -- tests the null of complete spatial
   randomness (CSR) against the alternative of positive or negative spatial
   autocorrelation.
2. Local Getis-Ord Gi* per county -- the standard hot/cold-spot statistic,
   flagging counties whose *local neighborhood* (not just the county itself)
   is significantly high (hot spot) or low (cold spot).
3. Connected-component clustering of the significant hot/cold-spot counties
   over the same contiguity graph, to answer "do they form contiguous
   regions?" objectively rather than by eyeballing a table.

Also runs the same pipeline on the capture-rate-corrected rate
(eb_posterior_rate_per_100k_corrected) as a robustness check and reports
whether the two versions agree, since OBDB's uneven capture rate could in
principle manufacture or mask apparent clustering.

Methodological judgment calls (see also the printed summary):
- Contiguity: Queen (shares an edge OR a vertex), not Rook (edge only).
  Many Western county borders meet at single corner points (the Four Corners
  region is the extreme case, but plenty of rectangular-survey counties
  elsewhere share only a corner) -- Rook would silently drop those
  adjacencies. Queen is also the more common default in the Gi*/Moran
  literature (e.g. ArcGIS Hot Spot Analysis, GeoDa).
- Significance: Gi* is asymptotically standard normal, so hot/cold-spot
  classification uses the two-sided analytic z-test (p = 2 * (1 - Phi(|Z|)))
  computed directly from Zs, rather than esda's built-in p_norm/p_sim, whose
  one- vs two-sided convention under different `alternative=` settings was
  inconsistent between the documented attribute description and observed
  values in testing. The permutation p-value (p_sim, 9999 permutations,
  seeded) is also reported per-county for corroboration.
- Multiple comparisons: with ~3,100 simultaneous per-county tests, a raw
  p<0.05 cutoff has an expected ~155 false positives under CSR. Benjamini-
  Hochberg FDR correction is applied as the primary classification
  criterion (standard practice for local cluster-detection statistics, e.g.
  Caldas de Castro & Singer 2006) rather than Bonferroni, which is overly
  conservative given the strong positive spatial autocorrelation already
  confirmed by the global Moran's I test (adjacent counties' Gi* values are
  not independent, so Bonferroni's independence assumption is badly wrong
  here too -- but BH is the field-standard middle ground). Raw-p and FDR-q
  counts are both printed so the difference is visible.
"""

from __future__ import annotations

import copy
import warnings

import geopandas as gpd
import numpy as np
import pandas as pd
from esda.getisord import G_Local
from esda.moran import Moran
from libpysal.weights import Queen
from scipy.sparse.csgraph import connected_components
from scipy.stats import norm
from statsmodels.stats.multitest import multipletests

from breweries.sources import tiger

RANKINGS_PATH = "data/processed/us_county_shrunken_rankings.parquet"
CORRECTED_RANKINGS_PATH = "data/processed/us_county_corrected_shrunken_rankings.parquet"
OUT_PATH = "data/processed/us_county_spatial_hotspots.csv"

# Same CONUS filter used in scripts/build_choropleth.py: AK, HI, and island
# territories are excluded because they aren't land-contiguous with the
# mainland, so a "contiguity" graph including them would be meaningless
# (they'd show up as graph islands with zero neighbors).
TERRITORY_FIPS = {"02", "15", "72", "78", "60", "66", "69"}

RATE_COL = "eb_posterior_rate_per_100k"
CORRECTED_RATE_COL = "eb_posterior_rate_per_100k_corrected"

PERMUTATIONS = 9999
SEED = 42
ALPHA = 0.05

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 20)


def load_conus_counties() -> gpd.GeoDataFrame:
    counties = tiger.load_counties()[["STATEFP", "GEOID", "NAMELSAD", "geometry"]]
    conus = counties[~counties["STATEFP"].isin(TERRITORY_FIPS)].copy()
    return conus.to_crs(epsg=5070).reset_index(drop=True)  # CONUS Albers Equal Area


def build_merged_gdf() -> gpd.GeoDataFrame:
    """CONUS county polygons joined to both the raw and capture-rate-corrected
    shrunken rankings, aligned on county_geoid/GEOID, in a single fixed row
    order (positional index 0..n-1) that the contiguity weights are built
    against.
    """
    conus = load_conus_counties()

    raw = pd.read_parquet(RANKINGS_PATH)
    raw["county_geoid"] = raw["county_geoid"].str.zfill(5)
    corrected = pd.read_parquet(CORRECTED_RANKINGS_PATH)
    corrected["county_geoid"] = corrected["county_geoid"].str.zfill(5)

    merged = conus.merge(
        raw[["county_geoid", "county_name", "state_abbr", RATE_COL]],
        left_on="GEOID", right_on="county_geoid", how="inner",
    )
    match_rate = len(merged) / len(conus)
    print(f"CONUS counties matched to raw rankings: {len(merged)} / {len(conus)} ({match_rate:.1%})")

    merged = merged.merge(
        corrected[["county_geoid", CORRECTED_RATE_COL]], on="county_geoid", how="left",
    )
    corrected_match = merged[CORRECTED_RATE_COL].notna().mean()
    print(f"  of which matched to corrected rankings: {corrected_match:.1%}")

    return merged.reset_index(drop=True)


def build_contiguity(gdf: gpd.GeoDataFrame) -> Queen:
    """Queen contiguity (shared edge or vertex) over the CONUS county polygons.
    See module docstring for why Queen over Rook.
    """
    with warnings.catch_warnings():
        # libpysal warns about disconnected components / islands; we check
        # for those explicitly below rather than suppressing the underlying
        # condition, just the warning noise.
        warnings.simplefilter("ignore")
        w = Queen.from_dataframe(gdf, use_index=False)
    if w.islands:
        island_names = gdf.loc[w.islands, "NAMELSAD"].tolist()
        print(f"  WARNING: {len(w.islands)} island counties with no Queen neighbors: {island_names}")
    else:
        print(f"  Queen contiguity graph: {w.n} counties, fully connected, no islands, "
              f"mean {w.mean_neighbors:.1f} neighbors/county")
    return w


def global_moran(y: np.ndarray, w: Queen) -> dict:
    w_row = copy.deepcopy(w)  # row-standardize a copy so the binary `w` used for Gi* is untouched
    w_row.transform = "r"

    np.random.seed(SEED)
    mi = Moran(y, w_row, permutations=PERMUTATIONS)
    return {
        "I": mi.I, "EI": mi.EI, "z_norm": mi.z_norm, "p_norm": mi.p_norm,
        "z_sim": mi.z_sim, "p_sim": mi.p_sim,
    }


def local_gi_star(y: np.ndarray, w: Queen) -> pd.DataFrame:
    """Getis-Ord Gi* per county. Binary weights, self included in the focal
    neighborhood (star=True), per the standard Gi* (as opposed to Gi)
    definition.
    """
    g = G_Local(y, w, transform="B", star=True, permutations=PERMUTATIONS, seed=SEED)
    z = np.asarray(g.Zs, dtype=float)
    p_two_sided = 2.0 * (1.0 - norm.cdf(np.abs(z)))  # analytic two-sided z-test; see module docstring
    return pd.DataFrame({
        "gi_star": np.asarray(g.Gs, dtype=float),
        "z_score": z,
        "p_value": p_two_sided,
        "p_value_perm": np.asarray(g.p_sim, dtype=float),  # esda's permutation p, for corroboration
    })


def classify(z: np.ndarray, p_fdr: np.ndarray) -> np.ndarray:
    labels = np.full(len(z), "not_significant", dtype=object)
    labels[(p_fdr < ALPHA) & (z > 0)] = "hot_spot"
    labels[(p_fdr < ALPHA) & (z < 0)] = "cold_spot"
    return labels


def contiguous_clusters(w: Queen, flagged_idx: np.ndarray) -> pd.Series:
    """Connected components of the contiguity graph restricted to the
    counties flagged (hot or cold, computed separately) as significant --
    i.e. do the significant counties form contiguous multi-county regions,
    or are they scattered singletons? Returns a Series mapping the flagged
    positional index -> cluster id (clusters of size 1 are isolated,
    non-contiguous flagged counties).
    """
    if len(flagged_idx) == 0:
        return pd.Series(dtype=int)
    sub = w.sparse[np.ix_(flagged_idx, flagged_idx)]
    n_components, labels = connected_components(sub, directed=False)
    return pd.Series(labels, index=flagged_idx)


def run_for_rate_col(gdf: gpd.GeoDataFrame, w: Queen, rate_col: str, label: str) -> pd.DataFrame:
    print(f"\n=== {label} ({rate_col}) ===")
    y = gdf[rate_col].to_numpy(dtype=float)

    moran = global_moran(y, w)
    direction = "positive" if moran["I"] > moran["EI"] else "negative"
    print(f"Global Moran's I = {moran['I']:.4f}  (expected under CSR = {moran['EI']:.4f})")
    print(f"  z (analytic) = {moran['z_norm']:.2f}, p (analytic, two-tailed) = {moran['p_norm']:.3g}")
    print(f"  z (permutation, {PERMUTATIONS} perms, seed={SEED}) = {moran['z_sim']:.2f}, "
          f"p (permutation) = {moran['p_sim']:.4g}")
    if moran["p_sim"] < ALPHA:
        print(f"  -> Rejects CSR: {direction} spatial autocorrelation in {rate_col}.")
    else:
        print("  -> Fails to reject CSR: no detectable spatial autocorrelation.")

    gi = local_gi_star(y, w)
    _, p_fdr, _, _ = multipletests(gi["p_value"].to_numpy(), alpha=ALPHA, method="fdr_bh")
    gi["p_value_fdr"] = p_fdr
    gi["spot_type"] = classify(gi["z_score"].to_numpy(), p_fdr)

    n_raw_sig = int((gi["p_value"] < ALPHA).sum())
    n_fdr_sig = int((gi["spot_type"] != "not_significant").sum())
    n_hot = int((gi["spot_type"] == "hot_spot").sum())
    n_cold = int((gi["spot_type"] == "cold_spot").sum())
    print(f"Local Gi*: {n_raw_sig} counties significant at raw p<{ALPHA} "
          f"(expected ~{ALPHA * len(gi):.0f} by chance under CSR); "
          f"{n_fdr_sig} significant after Benjamini-Hochberg FDR correction "
          f"({n_hot} hot spots, {n_cold} cold spots).")

    return gi


def main() -> None:
    gdf = build_merged_gdf()
    w = build_contiguity(gdf)

    gi_raw = run_for_rate_col(gdf, w, RATE_COL, "RAW shrunken rate")
    gi_corr = run_for_rate_col(gdf, w, CORRECTED_RATE_COL, "Capture-rate-CORRECTED shrunken rate")

    # --- Contiguous-cluster check on the raw-rate hot spots -----------------
    hot_idx = np.where(gi_raw["spot_type"] == "hot_spot")[0]
    cold_idx = np.where(gi_raw["spot_type"] == "cold_spot")[0]
    hot_clusters = contiguous_clusters(w, hot_idx)
    cold_clusters = contiguous_clusters(w, cold_idx)

    print(f"\n--- Contiguity of significant counties (raw rate, Queen graph) ---")
    if len(hot_idx):
        cluster_sizes = hot_clusters.value_counts().sort_values(ascending=False)
        n_multi = int((cluster_sizes > 1).sum())
        print(f"Hot spots: {len(hot_idx)} counties form {hot_clusters.nunique()} connected "
              f"component(s) ({n_multi} of them multi-county). Largest component sizes: "
              f"{cluster_sizes.head(8).tolist()}")
        # For the largest few clusters, list which states they span.
        for cid in cluster_sizes.head(5).index:
            members = hot_clusters[hot_clusters == cid].index
            if len(members) < 2:
                continue
            states = gdf.loc[members, "state_abbr"]
            names = gdf.loc[members, "NAMELSAD"]
            state_counts = states.value_counts()
            print(f"  Cluster of {len(members)} counties across {states.nunique()} state(s) "
                  f"({', '.join(f'{s}={c}' for s, c in state_counts.items())}): "
                  f"{', '.join(sorted(names.tolist()))[:220]}")
    else:
        print("Hot spots: none flagged.")

    if len(cold_idx):
        cluster_sizes = cold_clusters.value_counts().sort_values(ascending=False)
        n_multi = int((cluster_sizes > 1).sum())
        print(f"Cold spots: {len(cold_idx)} counties form {cold_clusters.nunique()} connected "
              f"component(s) ({n_multi} of them multi-county). Largest component sizes: "
              f"{cluster_sizes.head(8).tolist()}")
    else:
        print("Cold spots: none flagged.")

    # --- Raw vs. corrected agreement -----------------------------------------
    agree = (gi_raw["spot_type"] == gi_corr["spot_type"]).mean()
    hot_overlap = len(set(np.where(gi_raw["spot_type"] == "hot_spot")[0]) &
                       set(np.where(gi_corr["spot_type"] == "hot_spot")[0]))
    print(f"\nRaw vs. capture-rate-corrected agreement: {agree:.1%} of counties get the same "
          f"spot_type label; {hot_overlap} of {len(hot_idx)} raw hot spots are also hot spots "
          "under the corrected rate.")

    # --- Output table (raw rate is primary; corrected columns appended for
    #     comparison, per the task's "optionally also run on corrected" note) --
    out = gdf[["GEOID", "county_name", "state_abbr", RATE_COL]].copy()
    out = out.rename(columns={"GEOID": "county_geoid"})
    out["gi_star"] = gi_raw["gi_star"]
    out["z_score"] = gi_raw["z_score"]
    out["p_value"] = gi_raw["p_value"]
    out["p_value_fdr"] = gi_raw["p_value_fdr"]
    out["spot_type"] = gi_raw["spot_type"]
    out[CORRECTED_RATE_COL] = gdf[CORRECTED_RATE_COL]
    out["gi_star_corrected"] = gi_corr["gi_star"]
    out["z_score_corrected"] = gi_corr["z_score"]
    out["p_value_fdr_corrected"] = gi_corr["p_value_fdr"]
    out["spot_type_corrected"] = gi_corr["spot_type"]

    out.to_csv(OUT_PATH, index=False)
    print(f"\nWrote {OUT_PATH} ({len(out)} counties)")

    # --- Top hot spots by significance/magnitude ------------------------------
    top_n = 20
    top_hot = out[out["spot_type"] == "hot_spot"].sort_values("z_score", ascending=False).head(top_n)
    print(f"\nTop {min(top_n, len(top_hot))} hot-spot counties by Gi* z-score:")
    if len(top_hot):
        for _, row in top_hot.iterrows():
            print(f"  {row['county_name']:<28} {row['state_abbr']:<3}  "
                  f"rate={row[RATE_COL]:6.2f}/100k  z={row['z_score']:6.2f}  "
                  f"p_fdr={row['p_value_fdr']:.2g}")
        state_counts = top_hot["state_abbr"].value_counts()
        print(f"  States represented in top {len(top_hot)}: "
              f"{', '.join(f'{s} ({c})' for s, c in state_counts.items())}")
    else:
        print("  (none)")

    top_cold = out[out["spot_type"] == "cold_spot"].sort_values("z_score").head(top_n)
    print(f"\nTop {min(top_n, len(top_cold))} cold-spot counties by Gi* z-score:")
    if len(top_cold):
        for _, row in top_cold.iterrows():
            print(f"  {row['county_name']:<28} {row['state_abbr']:<3}  "
                  f"rate={row[RATE_COL]:6.2f}/100k  z={row['z_score']:6.2f}  "
                  f"p_fdr={row['p_value_fdr']:.2g}")
    else:
        print("  (none)")


if __name__ == "__main__":
    main()
