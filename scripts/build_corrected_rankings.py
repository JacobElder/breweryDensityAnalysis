"""Capture-rate-corrected national county ranking, and how it compares to the
existing raw-shrunken ranking (data/processed/us_county_shrunken_rankings.parquet).

The correction model (src/breweries/capture_rate_model.py) is already applied
in scripts/build_national_county_dataset.py to produce `obdb_corrected`,
`capture_rate`, and `correction_source` on data/processed/us_county_analysis.parquet.
But nobody has re-ranked counties by the corrected estimate. Doing that naively
(just divide obdb_count by capture_rate and sort) would make small counties
extremely noisy -- e.g. a county with 2 observed breweries and a 45% capture
rate "jumps" to 4.4 corrected breweries, almost entirely due to sampling noise
in that 2-count, not a real signal.

So this script applies the SAME empirical-Bayes Poisson-Gamma shrinkage
(src/breweries/shrinkage.py::shrink_rates, exactly as called in
scripts/fit_national_models.py::fit_empirical_bayes_shrinkage) to the
corrected estimate, not just the raw one -- keeping the corrected ranking
methodologically apples-to-apples with the existing raw-shrunken ranking.

JUDGMENT CALL (flagged per the task instructions): shrink_rates()/
fit_poisson_gamma() is a Poisson-Gamma conjugate model and expects an integer
COUNT column, but obdb_corrected is a continuous quantity (obdb_count /
capture_rate). There is no continuous generalization of Poisson-Gamma
shrinkage already built in this project, so this script rounds
obdb_corrected to the nearest integer (`obdb_corrected_rounded`) and feeds
that to shrink_rates() as the count column, with adults_21plus as the
exposure -- exactly mirroring how obdb_count/adults_21plus is used for the
raw ranking. This is an approximation: rounding discards some information
in the corrected estimate's fractional part, and it treats the corrected
estimate as if it carried the same sampling variance structure as a raw
Poisson count, when part of its uncertainty actually comes from the
capture-rate correction itself (wider for pooled_extrapolation counties than
for calibrated-state counties). It's the most consistent approach available
without building a new shrinkage estimator, and it still accomplishes the
stated goal: damping small-county noise in the corrected estimate the same
way it's already damped in the raw estimate. A more rigorous approach would
propagate correction uncertainty (corrected_low/corrected_high, computed
below from apply_correction) into the shrinkage prior itself -- flagged here
as a possible follow-up, not implemented.
"""

from __future__ import annotations

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import BoundaryNorm, LinearSegmentedColormap
from matplotlib.patches import Patch

from breweries.capture_rate_model import apply_correction
from breweries.map_labels import LabelCandidate, place_labels
from breweries.shrinkage import shrink_rates
from breweries.sources import tiger

ANALYSIS_PATH = "data/processed/us_county_analysis.parquet"
RAW_SHRUNKEN_PATH = "data/processed/us_county_shrunken_rankings.parquet"
POPULATION_FLOOR = 50_000
MAX_AUTO_LABELS = 22
ANCHOR_EXCLUSION_RADIUS_M = 80_000  # ~50 miles; skip an auto-label this close to a placed anchor

OUT_CORRECTED_RANKINGS = "data/processed/us_county_corrected_shrunken_rankings.parquet"
OUT_COMPARISON = "data/processed/us_county_raw_vs_corrected_rankings.csv"
OUT_MAP_FULL = "data/processed/us_brewery_density_choropleth_corrected.png"
OUT_MAP_FLOORED = "data/processed/us_brewery_density_choropleth_corrected_floored.png"

# Same visual style as scripts/build_choropleth.py, reused here for consistency
# (anchor cities placed first; remaining space filled by collision-aware,
# data-driven auto-labels via src/breweries/map_labels.py).
LABEL_CITIES = [
    ("Bend, OR", -121.3153, 44.0582),
    ("Asheville, NC", -82.5515, 35.5951),
    ("Portland, ME", -70.2553, 43.6591),
    ("Burlington, VT", -73.2121, 44.4759),
    ("Grand Rapids, MI", -85.6681, 42.9634),
    ("Fort Collins, CO", -105.0844, 40.5853),
    ("Boulder, CO", -105.2705, 40.0150),
    ("Traverse City, MI", -85.6206, 44.7631),
]

CMAP = LinearSegmentedColormap.from_list(
    "brewery_amber",
    ["#fff5e6", "#ffe0a3", "#ffc266", "#f2932e", "#c96a15", "#8a4008", "#4d2004"],
)
NO_DATA_COLOR = "#e8e8e8"
INSUFFICIENT_POP_COLOR = "#bfbfbf"

pd.set_option("display.width", 160)
pd.set_option("display.max_colwidth", 30)


def build_corrected_shrunken_rankings(df: pd.DataFrame) -> pd.DataFrame:
    """Re-derive per-county correction CIs (not stored in us_county_analysis.parquet
    -- only the point estimate obdb_corrected/capture_rate/correction_source are),
    then apply the same Poisson-Gamma shrinkage used for the raw ranking to the
    corrected count (rounded -- see module docstring for why).
    """
    df = df.copy()
    log_density = np.log(df["density_per_sqmi"].clip(lower=0.1))
    corrections = [
        apply_correction(row.obdb_count, row.state_abbr, ld)
        for row, ld in zip(df.itertuples(), log_density)
    ]
    df["correction_ci_low"] = [c.get("ci_low") for c in corrections]
    df["correction_ci_high"] = [c.get("ci_high") for c in corrections]
    df["obdb_corrected_low"] = [c.get("corrected_low", c["corrected_estimate"]) for c in corrections]
    df["obdb_corrected_high"] = [c.get("corrected_high", c["corrected_estimate"]) for c in corrections]

    # Sanity check against the point estimate already stored on disk.
    mismatch = (df["obdb_corrected"] - [c["corrected_estimate"] for c in corrections]).abs().max()
    if mismatch > 1e-6:
        raise ValueError(f"Re-derived obdb_corrected doesn't match stored column (max diff {mismatch})")

    df["obdb_corrected_rounded"] = df["obdb_corrected"].round().astype(int)

    df_shrunk = shrink_rates(df, "obdb_corrected_rounded", "adults_21plus")
    df_shrunk = df_shrunk.rename(columns={
        "eb_posterior_rate": "eb_posterior_rate_corrected",
        "eb_posterior_rate_per_100k": "eb_posterior_rate_per_100k_corrected",
        "eb_ci_low_per_100k": "eb_ci_low_per_100k_corrected",
        "eb_ci_high_per_100k": "eb_ci_high_per_100k_corrected",
    })
    shape, rate = df_shrunk.attrs["gamma_shape"], df_shrunk.attrs["gamma_rate"]
    print(f"National mean CORRECTED rate (prior): {shape / rate * 100_000:.2f} per 100k adults 21+")
    print(f"Gamma shape={shape:.3f}, rate={rate:.2e}")
    return df_shrunk


def build_comparison_table(df_corrected: pd.DataFrame) -> pd.DataFrame:
    raw = pd.read_parquet(RAW_SHRUNKEN_PATH)[[
        "county_geoid", "county_name", "state_abbr", "adults_21plus", "obdb_count",
        "obdb_rate_per_100k_21plus", "eb_posterior_rate_per_100k",
    ]].rename(columns={"eb_posterior_rate_per_100k": "raw_shrunken_rate_per_100k"})

    corr = df_corrected[[
        "county_geoid", "obdb_corrected", "capture_rate", "correction_source",
        "corrected_rate_per_100k_21plus", "eb_posterior_rate_per_100k_corrected",
    ]].rename(columns={"eb_posterior_rate_per_100k_corrected": "corrected_shrunken_rate_per_100k"})

    merged = raw.merge(corr, on="county_geoid", how="inner")
    floored = merged[merged["adults_21plus"] >= POPULATION_FLOOR].copy()

    floored["raw_rank"] = floored["raw_shrunken_rate_per_100k"].rank(ascending=False, method="min").astype(int)
    floored["corrected_rank"] = floored["corrected_shrunken_rate_per_100k"].rank(ascending=False, method="min").astype(int)
    floored["rank_change"] = floored["raw_rank"] - floored["corrected_rank"]  # positive = moved UP (better) when corrected

    floored = floored.sort_values("rank_change", ascending=False)
    return floored


def build_auto_label_candidates(
    conus_albers: gpd.GeoDataFrame, anchor_points_albers: list[tuple[float, float]],
) -> list[LabelCandidate]:
    """Top-corrected-rate counties (population-floored), as label candidates in
    Albers meters, excluding any within ANCHOR_EXCLUSION_RADIUS_M of a placed
    anchor. NAMELSAD (not bare NAME) disambiguates Virginia's independent
    cities from same-named counties -- see build_choropleth.py for the case.
    """
    value_col = "eb_posterior_rate_per_100k_corrected"
    pool = conus_albers[
        (conus_albers["adults_21plus"] >= POPULATION_FLOOR) & conus_albers[value_col].notna()
    ].sort_values(value_col, ascending=False)

    candidates = []
    for _, row in pool.iterrows():
        cx, cy = row.geometry.centroid.x, row.geometry.centroid.y
        too_close = any(
            ((cx - ax) ** 2 + (cy - ay) ** 2) ** 0.5 < ANCHOR_EXCLUSION_RADIUS_M
            for ax, ay in anchor_points_albers
        )
        if too_close:
            continue
        label = f"{row['NAMELSAD']}, {row['state_abbr']}"
        candidates.append(LabelCandidate(text=label, x=cx, y=cy, priority=float(row[value_col])))
    return candidates


def build_map(gdf: gpd.GeoDataFrame, out_path: str, floor: int | None) -> None:
    gdf = gdf.copy()
    if floor is not None:
        gdf["_below_floor"] = gdf["adults_21plus"] < floor
        gdf["_value"] = gdf["eb_posterior_rate_per_100k_corrected"].where(~gdf["_below_floor"])
    else:
        gdf["_below_floor"] = False
        gdf["_value"] = gdf["eb_posterior_rate_per_100k_corrected"]

    gdf_conus = gdf.to_crs(epsg=5070)
    territory_fips = {"02", "15", "72", "78", "60", "66", "69"}
    conus = gdf_conus[~gdf_conus["STATEFP"].isin(territory_fips)]
    alaska = gdf[gdf["STATEFP"] == "02"].to_crs(epsg=3338)
    hawaii = gdf[gdf["STATEFP"] == "15"].to_crs(epsg=3563)

    values = gdf["eb_posterior_rate_per_100k_corrected"].dropna()
    bins = [0, 1, 3, 6, 10, 15, values.max() + 1]
    labels = ["0-1", "1-3", "3-6", "6-10", "10-15", f"15-{values.max():.0f}"]
    norm = BoundaryNorm(bins, CMAP.N)

    def draw(ax, sub):
        sub.plot(ax=ax, column="_value", cmap=CMAP, norm=norm,
                  edgecolor="#888888", linewidth=0.2, missing_kwds={"color": NO_DATA_COLOR})
        below = sub[sub["_below_floor"]]
        if len(below):
            below.plot(ax=ax, color=INSUFFICIENT_POP_COLOR, edgecolor="#888888", linewidth=0.2)
        ax.set_axis_off()

    fig = plt.figure(figsize=(16, 10), facecolor="white")
    ax = fig.add_axes((0.02, 0.08, 0.96, 0.86))
    ax.set_facecolor("white")
    draw(ax, conus)

    title = "US Brewery Density by County (capture-rate corrected)"
    subtitle = ("Corrected-shrunken rate per 100,000 adults 21+ (obdb_corrected via "
                "capture_rate_model, then empirical Bayes shrinkage)")
    if floor is not None:
        title += " (population-floored)"
        subtitle = (f"Counties under {floor:,} adults 21+ shown gray, not colored — shrinkage "
                    "reduces but doesn't eliminate small-county noise")
    ax.set_title(f"{title}\n{subtitle}", fontsize=14, fontweight="bold", pad=14)

    ax_ak = fig.add_axes((0.02, 0.05, 0.20, 0.22))
    draw(ax_ak, alaska)
    ax_ak.set_title("AK", fontsize=9)

    ax_hi = fig.add_axes((0.20, 0.05, 0.10, 0.14))
    draw(ax_hi, hawaii)
    ax_hi.set_title("HI", fontsize=9)

    legend_elems = [Patch(facecolor=CMAP(norm((bins[i] + bins[i + 1]) / 2)), edgecolor="#888888",
                           label=labels[i]) for i in range(len(labels))]
    if floor is not None:
        legend_elems.append(Patch(facecolor=INSUFFICIENT_POP_COLOR, edgecolor="#888888",
                                   label=f"< {floor:,} adults 21+"))
    legend_elems.append(Patch(facecolor=NO_DATA_COLOR, edgecolor="#888888", label="No data"))
    legend = ax.legend(handles=legend_elems, loc="lower left", bbox_to_anchor=(0.33, -0.02),
                        title="Corrected breweries\nper 100k adults 21+", fontsize=9, title_fontsize=10, frameon=False)

    # Reserve the legend's own footprint so auto-labels don't get placed on top of it.
    fig.canvas.draw()
    reserved = [legend.get_window_extent(renderer=fig.canvas.get_renderer())]

    cities_gdf = gpd.GeoDataFrame(
        {"label": [c[0] for c in LABEL_CITIES]},
        geometry=gpd.points_from_xy([c[1] for c in LABEL_CITIES], [c[2] for c in LABEL_CITIES]),
        crs="EPSG:4326",
    ).to_crs(epsg=5070)
    anchor_candidates = [
        LabelCandidate(text=label, x=geom.x, y=geom.y, priority=1e9 - i)
        for i, (label, geom) in enumerate(zip(cities_gdf["label"], cities_gdf.geometry))
    ]
    n_anchors = place_labels(fig, ax, anchor_candidates, max_labels=len(anchor_candidates),
                              reserved_boxes=reserved)

    anchor_points = [(geom.x, geom.y) for geom in cities_gdf.geometry]
    auto_candidates = build_auto_label_candidates(conus, anchor_points)
    fig.canvas.draw()
    reserved_after_anchors = reserved + [
        t.get_window_extent(renderer=fig.canvas.get_renderer())
        for t in ax.texts
    ]
    n_auto = place_labels(fig, ax, auto_candidates, max_labels=MAX_AUTO_LABELS,
                           reserved_boxes=reserved_after_anchors)
    print(f"  Labels placed: {n_anchors} anchors + {n_auto} auto (of {len(auto_candidates)} candidates)")

    fig.text(0.5, 0.01,
              "Sources: Open Brewery DB, Census ACS 5-year (2020-2024), empirical Bayes shrinkage, "
              "and the 13-state capture-rate correction model (calibrated states use their empirical "
              "rate; other states use the pooled WLS-regression rate + density adjustment, capped at "
              "1.0). Corrected counts are rounded to the nearest integer before shrinkage — see "
              "scripts/build_corrected_rankings.py docstring for why.",
              ha="center", fontsize=7.5, color="#555555", wrap=True)

    fig.savefig(out_path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Wrote {out_path}")


def main() -> None:
    df = pd.read_parquet(ANALYSIS_PATH)
    print("=" * 70)
    print("Capture-rate-corrected + shrunken county ranking")
    print("=" * 70)

    df_corrected = build_corrected_shrunken_rankings(df)
    df_corrected.to_parquet(OUT_CORRECTED_RANKINGS, index=False)
    print(f"\nWrote {OUT_CORRECTED_RANKINGS} ({len(df_corrected)} counties)")

    print(f"\nTop 20 by corrected-shrunken posterior rate (population >= {POPULATION_FLOOR:,}):")
    top = df_corrected[df_corrected["adults_21plus"] >= POPULATION_FLOOR].sort_values(
        "eb_posterior_rate_per_100k_corrected", ascending=False)
    print(top[["county_name", "state_abbr", "obdb_count", "obdb_corrected", "capture_rate",
               "correction_source", "adults_21plus", "eb_posterior_rate_per_100k_corrected"]]
          .head(20).to_string(index=False))

    comparison = build_comparison_table(df_corrected)
    comparison.to_csv(OUT_COMPARISON, index=False)
    print(f"\nWrote {OUT_COMPARISON} ({len(comparison)} counties, population >= {POPULATION_FLOOR:,}, "
          "sorted by rank_change = raw_rank - corrected_rank, descending: biggest movers UP first)")

    print("\n" + "=" * 70)
    print("RAW-SHRUNKEN TOP 20")
    print("=" * 70)
    raw_top20 = comparison.sort_values("raw_rank").head(20)
    print(raw_top20[["county_name", "state_abbr", "raw_rank", "corrected_rank", "rank_change",
                      "raw_shrunken_rate_per_100k", "corrected_shrunken_rate_per_100k"]].to_string(index=False))

    print("\n" + "=" * 70)
    print("CORRECTED-SHRUNKEN TOP 20")
    print("=" * 70)
    corrected_top20 = comparison.sort_values("corrected_rank").head(20)
    print(corrected_top20[["county_name", "state_abbr", "raw_rank", "corrected_rank", "rank_change",
                            "raw_shrunken_rate_per_100k", "corrected_shrunken_rate_per_100k"]].to_string(index=False))

    raw_set = set(raw_top20["county_geoid"]) if "county_geoid" in raw_top20 else set(raw_top20.index)
    print("\n" + "=" * 70)
    print("NEW ENTRANTS to corrected top 20 (not in raw top 20):")
    print("=" * 70)
    new_entrants = corrected_top20[~corrected_top20["county_geoid"].isin(comparison.loc[comparison["raw_rank"] <= 20, "county_geoid"])]
    print(new_entrants[["county_name", "state_abbr", "raw_rank", "corrected_rank", "rank_change"]].to_string(index=False))

    print("\n" + "=" * 70)
    print("BIGGEST MOVERS UP (rank_change descending), top 15:")
    print("=" * 70)
    print(comparison.head(15)[["county_name", "state_abbr", "raw_rank", "corrected_rank", "rank_change",
                                "capture_rate", "correction_source"]].to_string(index=False))

    print("\n" + "=" * 70)
    print("BIGGEST MOVERS DOWN (rank_change ascending), top 15:")
    print("=" * 70)
    print(comparison.tail(15).sort_values("rank_change")[["county_name", "state_abbr", "raw_rank",
          "corrected_rank", "rank_change", "capture_rate", "correction_source"]].to_string(index=False))

    print("\n" + "=" * 70)
    print("Focus states: GA (48%), VA (46%), PA (49%), TX (44% -- actually TX capture_rate is "
          "clipped at 1.0 since raw empirical rate is 122%, see capture_rate_model.py)")
    print("=" * 70)
    for st in ["GA", "VA", "PA", "TX"]:
        sub = comparison[comparison["state_abbr"] == st].sort_values("corrected_rank")
        print(f"\n{st} counties (population >= {POPULATION_FLOOR:,}), by corrected rank:")
        print(sub[["county_name", "raw_rank", "corrected_rank", "rank_change",
                   "capture_rate", "raw_shrunken_rate_per_100k", "corrected_shrunken_rate_per_100k"]]
              .head(10).to_string(index=False))

    # Choropleth: reuse the same visual style as scripts/build_choropleth.py.
    print("\n" + "=" * 70)
    print("Building corrected choropleth maps")
    print("=" * 70)
    counties = tiger.load_counties()[["STATEFP", "GEOID", "NAMELSAD", "geometry"]]
    map_df = df_corrected[["county_geoid", "eb_posterior_rate_per_100k_corrected",
                            "adults_21plus", "state_abbr"]].copy()
    map_df["county_geoid"] = map_df["county_geoid"].str.zfill(5)
    merged = counties.merge(map_df, left_on="GEOID", right_on="county_geoid", how="left")
    match_rate = merged["eb_posterior_rate_per_100k_corrected"].notna().mean()
    print(f"Counties matched to corrected rate data: {match_rate:.1%}")

    build_map(merged, OUT_MAP_FULL, floor=None)
    build_map(merged, OUT_MAP_FLOORED, floor=POPULATION_FLOOR)


if __name__ == "__main__":
    main()
