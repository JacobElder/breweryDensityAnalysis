"""Three-source (OBDB x OSM x state-licensee) log-linear capture-recapture model,
for the two calibration states where the state registry is record-level with
coordinates (Colorado) or at least record-level with county (Oregon) rather
than only a county-aggregate report (North Carolina).

BACKGROUND / WHY THIS SCRIPT EXISTS
------------------------------------
scripts/nc_capture_recapture.py fit a naive two-sample Chapman estimator
between OBDB and OSM for North Carolina and got N_hat ~= 797, about 1.9x the
ABC/BA anchor of ~420 (see docs/methods_memo.md section 5.3). The diagnosis:
OBDB and OSM are both crowdsourced/volunteer-maintained lists, so a brewery's
odds of being listed on one correlate with its odds of being listed on the
other (shared "online visibility" trait) -- this is exactly the violated-
independence condition that inflates two-sample capture-recapture estimates.

This script asks whether a proper three-sample model can do better in the two
states where a third, structurally-different list actually exists at record
level: a state liquor-licensing registry (Colorado Liquor Enforcement, Oregon
OLCC). The state registry's capture mechanism -- you must hold a physical
production license to legally operate -- is plausibly uncorrelated with
"being featured on a crowdsourced beer-enthusiast list," which is exactly the
kind of third list the log-linear capture-recapture literature (Fienberg
1972; the "no three-way interaction" model summarized in the IWGDMF 1995
consensus statement, and implemented in R's Rcapture package) uses to *model*
pairwise list dependence explicitly instead of assuming it away.

METHOD
------
For each state:
  1. Match OBDB, OSM, and the state registry to each other pairwise by name
     similarity + geographic proximity (Colorado: the registry has lat/lon
     directly) or name similarity + same county (Oregon: the OLCC feed has no
     lat/lon in the fields actually returned by the Socrata API -- only a
     physical address string and a county field -- so proximity matching
     isn't available for OLCC pairs; OBDB-OSM in Oregon still uses distance,
     since both of those *do* have coordinates). This deviates from the task
     brief's assumption that OR licensee data has coordinates; it doesn't, as
     confirmed against the live feed (see NOTE below), so this script backs
     off to the coarser but still meaningful county+name match for any pair
     involving OLCC.
  2. Union-find the three pairwise match sets into brewery "identities" and
     build the 2x2x2 capture-history table (present/absent on each of the 3
     lists), which has 7 observable cells (the (0,0,0) "seen by nobody" cell
     is definitionally unobserved).
  3. Fit a Poisson log-linear model on the 7 observed cells with all three
     pairwise interaction terms but no three-way interaction:
         count ~ obdb + osm + liq + obdb:osm + obdb:liq + osm:liq
     This model has exactly 7 free parameters for 7 data points (0 residual
     df) -- it is *just-identified*, which is a known, expected property of
     this classic estimator, not a bug: the point of the model isn't to fit
     the observed cells (any saturated-enough model does that) but to
     extrapolate them, under the assumption that no 3-way interaction exists,
     to estimate the unobserved (0,0,0) cell. N_hat = n_observed + that
     extrapolated cell.
  4. Fit the independence model (main effects only, no interactions) too --
     it has 3 residual df, so its deviance is a real likelihood-ratio test of
     "do we need the pairwise interaction terms at all" (equivalently, of
     whether any list-pair is significantly non-independent).
  5. Compute the naive two-source (OBDB x OSM) Chapman estimate on the exact
     same matched identities, as the baseline this script is trying to beat.
  6. Compare 3-source N_hat, independence-model N_hat, and naive 2-source
     N_hat against the state's own licensee count and the Brewers Association
     total -- the two truth anchors this project already trusts (see
     docs/methods_memo.md, checkpoint 2/3 in build_co_county_dataset.py /
     build_or_county_dataset.py).

This script does not modify capture_recapture.py, co_liquor.py, or_olcc.py,
or any pipeline script. It only reads via their public functions and writes
new files under data/processed/.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from rapidfuzz import fuzz
from scipy.stats import chi2

from breweries.capture_recapture import lincoln_petersen, match_records, normalize_name
from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import co_liquor, obdb, or_olcc, osm

pd.set_option("display.width", 160)
pd.set_option("display.max_colwidth", 40)

OUT_DIR = Path("data/processed")

# Truth anchors already established and cited elsewhere in this project.
BA_CO_TOTAL_2025 = 423  # scripts/build_co_county_dataset.py
BA_OR_TOTAL_2025 = 297  # scripts/build_or_county_dataset.py


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def match_by_county_name(
    df_a: pd.DataFrame, df_b: pd.DataFrame,
    name_a: str, name_b: str, group_a: str, group_b: str,
    name_threshold: float = 65,
) -> pd.DataFrame:
    """Greedy name-similarity matching restricted to same-group (here: county)
    candidates, for source pairs where at least one side has no coordinates.

    Mirrors capture_recapture.match_records's greedy nearest-then-best-name
    logic, but substitutes exact group equality for the distance threshold.
    Each row in df_a matches at most one row in df_b and vice versa.
    """
    a = df_a.reset_index(drop=True).copy()
    b = df_b.reset_index(drop=True).copy()
    a["_norm_name"] = a[name_a].map(normalize_name)
    b["_norm_name"] = b[name_b].map(normalize_name)
    a["_group"] = a[group_a]
    b["_group"] = b[group_b]

    by_group: dict = {}
    for j in range(len(b)):
        by_group.setdefault(b.loc[j, "_group"], []).append(j)

    used_b = set()
    match_idx = np.full(len(a), -1, dtype=int)
    match_score = np.full(len(a), np.nan)

    for i in range(len(a)):
        grp = a.loc[i, "_group"]
        if pd.isna(grp):
            continue
        candidates = [j for j in by_group.get(grp, []) if j not in used_b]
        if not candidates:
            continue

        best_j, best_score = None, -1
        for j in candidates:
            score = fuzz.token_sort_ratio(a.loc[i, "_norm_name"], b.loc[j, "_norm_name"])
            if score > best_score:
                best_j, best_score = j, score

        if best_score >= name_threshold:
            match_idx[i] = best_j
            match_score[i] = best_score
            used_b.add(best_j)

    a["matched_b_index"] = match_idx
    a["match_name_score"] = match_score
    a["match_distance_m"] = np.nan  # not applicable for this matcher
    return a


# ---------------------------------------------------------------------------
# Union-find identity resolution across 3 pairwise match sets
# ---------------------------------------------------------------------------

def _find(parent: list[int], x: int) -> int:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _union(parent: list[int], x: int, y: int) -> None:
    rx, ry = _find(parent, x), _find(parent, y)
    if rx != ry:
        parent[rx] = ry


def build_identities(
    n_obdb: int, n_osm: int, n_liq: int,
    match_obdb_osm: pd.DataFrame, match_obdb_liq: pd.DataFrame, match_osm_liq: pd.DataFrame,
) -> tuple[pd.DataFrame, int]:
    """Union the 3 pairwise greedy matches into brewery identities and build
    the per-identity capture history (present in obdb / osm / liq).

    Returns (identity_df, n_ambiguous) where n_ambiguous counts identities
    that ended up containing more than one record from the same source
    (a transitive-chaining artifact of combining 3 independent pairwise
    matches) -- these are kept (first member wins) but flagged so match
    quality can be audited rather than silently trusted.
    """
    offset_osm = n_obdb
    offset_liq = n_obdb + n_osm
    total = n_obdb + n_osm + n_liq
    parent = list(range(total))

    for i, j in enumerate(match_obdb_osm["matched_b_index"].to_numpy()):
        if j >= 0:
            _union(parent, i, offset_osm + int(j))
    for i, k in enumerate(match_obdb_liq["matched_b_index"].to_numpy()):
        if k >= 0:
            _union(parent, i, offset_liq + int(k))
    for j, k in enumerate(match_osm_liq["matched_b_index"].to_numpy()):
        if k >= 0:
            _union(parent, offset_osm + j, offset_liq + int(k))

    groups: dict[int, list[int]] = {}
    for idx in range(total):
        r = _find(parent, idx)
        groups.setdefault(r, []).append(idx)

    records = []
    n_ambiguous = 0
    for members in groups.values():
        obdb_m = [m for m in members if m < offset_osm]
        osm_m = [m for m in members if offset_osm <= m < offset_liq]
        liq_m = [m for m in members if m >= offset_liq]
        if len(obdb_m) > 1 or len(osm_m) > 1 or len(liq_m) > 1:
            n_ambiguous += 1
        records.append({
            "obdb": int(len(obdb_m) > 0),
            "osm": int(len(osm_m) > 0),
            "liq": int(len(liq_m) > 0),
            "obdb_idx": obdb_m[0] if obdb_m else -1,
            "osm_idx": (osm_m[0] - offset_osm) if osm_m else -1,
            "liq_idx": (liq_m[0] - offset_liq) if liq_m else -1,
            "n_obdb_in_group": len(obdb_m),
            "n_osm_in_group": len(osm_m),
            "n_liq_in_group": len(liq_m),
        })
    identity_df = pd.DataFrame.from_records(records)
    return identity_df, n_ambiguous


# ---------------------------------------------------------------------------
# Log-linear capture-recapture model
# ---------------------------------------------------------------------------

def fit_loglinear_models(identity_df: pd.DataFrame) -> dict:
    """Fit the no-3-way-interaction Poisson log-linear model and the
    independence model on the 2x2x2 (minus the unobserved 000 cell)
    capture-history table, and extrapolate N_hat from each.
    """
    cell_counts = (
        identity_df.groupby(["obdb", "osm", "liq"]).size().rename("count").reset_index()
    )
    cell_counts = cell_counts[~((cell_counts.obdb == 0) & (cell_counts.osm == 0) & (cell_counts.liq == 0))]
    assert len(cell_counts) == 7, f"expected 7 observed capture-history cells, got {len(cell_counts)}"

    n_observed = int(identity_df.shape[0])
    pred_row = pd.DataFrame({"obdb": [0], "osm": [0], "liq": [0]})

    full_model = smf.glm(
        "count ~ obdb + osm + liq + obdb:osm + obdb:liq + osm:liq",
        data=cell_counts, family=sm.families.Poisson(),
    ).fit()
    full_pred = full_model.get_prediction(pred_row).summary_frame(alpha=0.05)
    n0_full = float(full_pred["mean"].iloc[0])
    n_hat_full = n_observed + n0_full
    n_hat_full_ci = (n_observed + float(full_pred["mean_ci_lower"].iloc[0]),
                      n_observed + float(full_pred["mean_ci_upper"].iloc[0]))

    indep_model = smf.glm(
        "count ~ obdb + osm + liq", data=cell_counts, family=sm.families.Poisson(),
    ).fit()
    indep_pred = indep_model.get_prediction(pred_row).summary_frame(alpha=0.05)
    n0_indep = float(indep_pred["mean"].iloc[0])
    n_hat_indep = n_observed + n0_indep
    n_hat_indep_ci = (n_observed + float(indep_pred["mean_ci_lower"].iloc[0]),
                       n_observed + float(indep_pred["mean_ci_upper"].iloc[0]))

    # Full (2-way) model is saturated (0 residual df) by construction, so its
    # deviance is 0 and the independence model's deviance is directly the
    # likelihood-ratio statistic for "are all 3 pairwise interactions jointly
    # zero", df = 7 params (full) - 4 params (indep) = 3.
    lr_stat = float(indep_model.deviance - full_model.deviance)
    lr_df = full_model.df_model - indep_model.df_model  # 6 - 3 = 3
    lr_p = float(1 - chi2.cdf(lr_stat, df=lr_df))

    return {
        "cell_counts": cell_counts,
        "n_observed": n_observed,
        "full_model": full_model,
        "indep_model": indep_model,
        "n_hat_full": n_hat_full,
        "n_hat_full_ci": n_hat_full_ci,
        "n_hat_indep": n_hat_indep,
        "n_hat_indep_ci": n_hat_indep_ci,
        "lr_stat": lr_stat,
        "lr_df": lr_df,
        "lr_p": lr_p,
    }


# ---------------------------------------------------------------------------
# Per-state pipeline
# ---------------------------------------------------------------------------

def load_obdb_osm(state_name: str, state_code: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    df_obdb = obdb.load_state(state_name)
    df_obdb = obdb.apply_inclusion_rule(df_obdb, state_code)
    df_obdb = fill_missing_coords(
        df_obdb, "id", "latitude", "longitude", "address_1", "city",
        "state_province", "postal_code", f"obdb_{state_code.lower()}_3src",
    )
    df_obdb = df_obdb[df_obdb["latitude"].notna() & df_obdb["longitude"].notna()].reset_index(drop=True)

    df_osm = osm.load_state(state_code)
    return df_obdb, df_osm


def spot_check(matched: pd.DataFrame, label: str, name_a_col: str, name_b_df: pd.DataFrame,
                name_b_col: str, n: int = 15) -> None:
    """Print n matched and a few unmatched examples for manual audit."""
    print(f"\n--- Match-quality spot check: {label} ---")
    got = matched[matched["matched_b_index"] >= 0].copy()
    print(f"{len(got)} matched / {len(matched)} total on the 'a' side")
    sample = got.sample(min(n, len(got)), random_state=42) if len(got) else got
    for _, row in sample.iterrows():
        b_name = name_b_df.loc[int(row["matched_b_index"]), name_b_col]
        dist = row.get("match_distance_m", np.nan)
        dist_str = f"{dist:.0f}m" if pd.notna(dist) else "n/a (county-match)"
        print(f"  '{row[name_a_col]}' <-> '{b_name}'  score={row['match_name_score']:.0f}  dist={dist_str}")
    unmatched = matched[matched["matched_b_index"] < 0]
    print(f"  {len(unmatched)} unmatched examples (first 5):")
    for _, row in unmatched.head(5).iterrows():
        print(f"    '{row[name_a_col]}' -- no match")


def run_state(
    state_label: str, state_name: str, state_code: str,
    df_liq: pd.DataFrame, liq_name_col: str, liq_group_col: str | None,
    liq_lat_col: str | None, liq_lon_col: str | None,
    truth_licensee: int, truth_ba: int,
) -> dict:
    print("\n" + "=" * 78)
    print(f"{state_label}: three-source (OBDB x OSM x licensee) capture-recapture")
    print("=" * 78)

    df_obdb, df_osm = load_obdb_osm(state_name, state_code)
    n_obdb, n_osm, n_liq = len(df_obdb), len(df_osm), len(df_liq)
    print(f"OBDB n={n_obdb}, OSM n={n_osm}, licensee n={n_liq}")

    use_distance_for_liq = liq_lat_col is not None and liq_lon_col is not None

    # OBDB x OSM: always distance-based (both sources have coordinates everywhere).
    match_obdb_osm = match_records(
        df_obdb, df_osm, name_a="name", name_b="name",
        lat_a="latitude", lon_a="longitude", lat_b="lat", lon_b="lon",
    )

    if use_distance_for_liq:
        match_obdb_liq = match_records(
            df_obdb, df_liq, name_a="name", name_b=liq_name_col,
            lat_a="latitude", lon_a="longitude", lat_b=liq_lat_col, lon_b=liq_lon_col,
        )
        match_osm_liq = match_records(
            df_osm, df_liq, name_a="name", name_b=liq_name_col,
            lat_a="lat", lon_a="lon", lat_b=liq_lat_col, lon_b=liq_lon_col,
        )
    else:
        # Licensee list has no coordinates (Oregon OLCC): fall back to
        # same-county + name-similarity matching. Requires OBDB/OSM to carry
        # an assigned county.
        df_obdb_geo = assign_geographies(df_obdb, "latitude", "longitude", state_code, f"obdb_{state_code.lower()}_geo")
        df_obdb_geo["county_clean"] = df_obdb_geo["county_name"].str.replace(" County", "", regex=False)
        df_osm_geo = assign_geographies(df_osm, "lat", "lon", state_code, f"osm_{state_code.lower()}_geo")
        df_osm_geo["county_clean"] = df_osm_geo["county_name"].str.replace(" County", "", regex=False)
        df_liq = df_liq.copy()
        df_liq["county_clean"] = df_liq[liq_group_col].astype(str).str.strip()

        match_obdb_liq = match_by_county_name(
            df_obdb_geo, df_liq, name_a="name", name_b=liq_name_col,
            group_a="county_clean", group_b="county_clean",
        )
        match_osm_liq = match_by_county_name(
            df_osm_geo, df_liq, name_a="name", name_b=liq_name_col,
            group_a="county_clean", group_b="county_clean",
        )

    spot_check(match_obdb_osm, f"{state_label} OBDB<->OSM", "name", df_osm, "name")
    spot_check(match_obdb_liq, f"{state_label} OBDB<->licensee", "name", df_liq, liq_name_col)
    spot_check(match_osm_liq, f"{state_label} OSM<->licensee", "name", df_liq, liq_name_col)

    identity_df, n_ambiguous = build_identities(
        n_obdb, n_osm, n_liq, match_obdb_osm, match_obdb_liq, match_osm_liq,
    )
    print(f"\n{len(identity_df)} distinct brewery identities resolved "
          f"({n_ambiguous} flagged ambiguous: >1 same-source record merged into one identity)")

    fit = fit_loglinear_models(identity_df)

    print("\nCapture-history cell counts:")
    print(fit["cell_counts"].to_string(index=False))

    print("\nFull (no-3-way-interaction) model coefficients:")
    print(fit["full_model"].params.to_string())
    print("\nIndependence model coefficients:")
    print(fit["indep_model"].params.to_string())
    print(f"\nLR test (indep vs. full 2-way model), df={fit['lr_df']}: "
          f"chi2={fit['lr_stat']:.2f}, p={fit['lr_p']:.4f}")

    # Naive 2-source (OBDB x OSM) baseline on the SAME matched identities.
    m_obdb_osm = int(((identity_df["obdb"] == 1) & (identity_df["osm"] == 1)).sum())
    naive = lincoln_petersen(n_obdb, n_osm, m_obdb_osm)

    # Diagnostic: the *other* two pairwise Chapman estimates (obdb-liq, osm-liq),
    # to check whether the 3-source blowup traces to one bad pair or is a
    # genuinely joint, multiplicative effect of all three pairs together.
    m_obdb_liq = int(((identity_df["obdb"] == 1) & (identity_df["liq"] == 1)).sum())
    m_osm_liq = int(((identity_df["osm"] == 1) & (identity_df["liq"] == 1)).sum())
    pw_obdb_liq = lincoln_petersen(n_obdb, n_liq, m_obdb_liq)
    pw_osm_liq = lincoln_petersen(n_osm, n_liq, m_osm_liq)
    print("\nPairwise 2-source Chapman estimates (diagnostic -- each pair alone, ignoring the third list):")
    print(f"  OBDB x OSM:     n1={n_obdb:4d} n2={n_osm:4d} m={m_obdb_osm:4d}  N_hat={naive['n_hat']:8.1f}")
    print(f"  OBDB x licensee: n1={n_obdb:4d} n2={n_liq:4d} m={m_obdb_liq:4d}  N_hat={pw_obdb_liq['n_hat']:8.1f}")
    print(f"  OSM x licensee:  n1={n_osm:4d} n2={n_liq:4d} m={m_osm_liq:4d}  N_hat={pw_osm_liq['n_hat']:8.1f}")

    print("\n" + "-" * 78)
    print("ESTIMATES vs. TRUTH")
    print("-" * 78)
    print(f"{'Naive 2-source (OBDB x OSM) Chapman N_hat':50s} {naive['n_hat']:8.1f}  "
          f"95% CI [{naive['ci_low']:.1f}, {naive['ci_high']:.1f}]")
    print(f"{'Independence-model (3-source, no interactions) N_hat':50s} {fit['n_hat_indep']:8.1f}  "
          f"95% CI [{fit['n_hat_indep_ci'][0]:.1f}, {fit['n_hat_indep_ci'][1]:.1f}]")
    print(f"{'3-source, no-3-way-interaction log-linear N_hat':50s} {fit['n_hat_full']:8.1f}  "
          f"95% CI [{fit['n_hat_full_ci'][0]:.1f}, {fit['n_hat_full_ci'][1]:.1f}]")
    print(f"{'State licensee registry count (truth anchor 1)':50s} {truth_licensee:8d}")
    print(f"{'Brewers Association total (truth anchor 2)':50s} {truth_ba:8d}")

    err_naive = naive["n_hat"] - truth_licensee
    err_full = fit["n_hat_full"] - truth_licensee
    err_indep = fit["n_hat_indep"] - truth_licensee
    print(f"\nError vs. licensee truth: naive={err_naive:+.1f} ({err_naive/truth_licensee:+.1%}), "
          f"independence={err_indep:+.1f} ({err_indep/truth_licensee:+.1%}), "
          f"3-source-2way={err_full:+.1f} ({err_full/truth_licensee:+.1%})")

    verdict = "BETTER" if abs(err_full) < abs(err_naive) else "NOT BETTER"
    print(f"VERDICT: 3-source no-3-way-interaction model is {verdict} than naive 2-source "
          f"({abs(err_full):.1f} vs {abs(err_naive):.1f} absolute error against licensee truth)")

    # --- write audit files ---
    name_col_map = {"obdb": "name", "osm": "name"}
    identity_out = identity_df.copy()
    identity_out["obdb_name"] = identity_out["obdb_idx"].map(
        lambda i: df_obdb.loc[i, "name"] if i >= 0 else None)
    identity_out["osm_name"] = identity_out["osm_idx"].map(
        lambda i: df_osm.loc[i, "name"] if i >= 0 else None)
    identity_out["liq_name"] = identity_out["liq_idx"].map(
        lambda i: df_liq.loc[i, liq_name_col] if i >= 0 else None)

    out_path = OUT_DIR / f"{state_code.lower()}_3source_capture_identities.csv"
    identity_out.to_csv(out_path, index=False)
    print(f"\nWrote {out_path}")

    return {
        "state": state_label,
        "n_obdb": n_obdb, "n_osm": n_osm, "n_liq": n_liq,
        "n_identities": len(identity_df), "n_ambiguous": n_ambiguous,
        "m_obdb_osm": m_obdb_osm,
        "naive_2source_n_hat": naive["n_hat"],
        "naive_2source_ci_low": naive["ci_low"], "naive_2source_ci_high": naive["ci_high"],
        "indep_3source_n_hat": fit["n_hat_indep"],
        "indep_3source_ci_low": fit["n_hat_indep_ci"][0], "indep_3source_ci_high": fit["n_hat_indep_ci"][1],
        "loglinear_3source_n_hat": fit["n_hat_full"],
        "loglinear_3source_ci_low": fit["n_hat_full_ci"][0], "loglinear_3source_ci_high": fit["n_hat_full_ci"][1],
        "lr_chi2": fit["lr_stat"], "lr_df": fit["lr_df"], "lr_p": fit["lr_p"],
        "coef_obdb_osm": fit["full_model"].params.get("obdb:osm", np.nan),
        "coef_obdb_liq": fit["full_model"].params.get("obdb:liq", np.nan),
        "coef_osm_liq": fit["full_model"].params.get("osm:liq", np.nan),
        "truth_licensee": truth_licensee, "truth_ba": truth_ba,
        "err_naive_vs_licensee": naive["n_hat"] - truth_licensee,
        "err_indep_vs_licensee": fit["n_hat_indep"] - truth_licensee,
        "err_loglinear_vs_licensee": fit["n_hat_full"] - truth_licensee,
        "pairwise_obdb_liq_n_hat": pw_obdb_liq["n_hat"],
        "pairwise_osm_liq_n_hat": pw_osm_liq["n_hat"],
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    df_co_liq = co_liquor.load()
    df_co_liq["match_name"] = df_co_liq["doing_business_as"].fillna(df_co_liq["licensee_name"])
    co_result = run_state(
        "Colorado", "Colorado", "CO",
        df_co_liq, "match_name", None, "lat", "lon",
        truth_licensee=len(df_co_liq), truth_ba=BA_CO_TOTAL_2025,
    )

    df_or_liq = or_olcc.load(include_additional_locations=False)
    df_or_liq["match_name"] = df_or_liq["trade_name"].fillna(df_or_liq["licensee_name"])
    print("\nNOTE: Oregon OLCC Socrata feed fields (checked against the live raw JSON) are "
          "license_number, trade_name, licensee_name, license_type, license_expired, "
          "effective_date, license_expires, renewal_district, physical_address, city, "
          "county, local_governing_body, lottery_on_premises, endorsements, "
          "secondary_to_primary_license -- there is no lat/lon field. or_olcc.py's own "
          "docstring already says this ('includes a county field directly ... no geocoding "
          "needed for county-level rollups'), which is a weaker claim than record-level "
          "coordinates. Any pair involving OLCC therefore uses same-county + name-similarity "
          "matching here, not distance matching.")
    or_result = run_state(
        "Oregon", "Oregon", "OR",
        df_or_liq, "match_name", "county", None, None,
        truth_licensee=len(df_or_liq), truth_ba=BA_OR_TOTAL_2025,
    )

    summary = pd.DataFrame.from_records([co_result, or_result])
    out_path = OUT_DIR / "three_source_capture_recapture_summary.csv"
    summary.to_csv(out_path, index=False)
    print(f"\n\nWrote {out_path}")
    print("\n" + "=" * 78)
    print("FINAL SUMMARY")
    print("=" * 78)
    cols = ["state", "n_obdb", "n_osm", "n_liq", "n_identities",
            "naive_2source_n_hat", "indep_3source_n_hat", "loglinear_3source_n_hat",
            "truth_licensee", "truth_ba"]
    print(summary[cols].to_string(index=False))


if __name__ == "__main__":
    main()
