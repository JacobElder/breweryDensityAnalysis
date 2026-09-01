"""Three-panel comparison choropleth showing the effect of empirical-Bayes
shrinkage and then capture-rate correction on the same underlying county-level
brewery density estimate:

  1. Raw observed rate (obdb_rate_per_100k_21plus) -- no shrinkage, noisy.
  2. Empirical-Bayes shrunken rate (eb_posterior_rate_per_100k) -- partial
     pooling toward the national mean damps small-county noise.
  3. Capture-rate-corrected + shrunken rate
     (eb_posterior_rate_per_100k_corrected) -- additionally adjusts for
     Open Brewery DB's known state-varying undercount.

Each stage previously only existed as a separate full-size static PNG
(scripts/build_choropleth.py for stage 2, scripts/build_corrected_rankings.py
for stage 3), so the effect of shrinkage and then correction -- e.g. Texas
counties drop substantially under correction because TX's raw capture rate is
already >100%, clipped to 1.0, so zero upward correction applies while other
states rise -- was not visible without manually flipping between images. This
script puts all three side by side on one shared color scale.

SIMPLIFICATION: this comparison figure drops the Alaska/Hawaii inset axes used
by scripts/build_choropleth.py and scripts/build_corrected_rankings.py and
shows CONUS only, to keep three panels compact and legible in one figure --
the point here is showing the three-stage transformation clearly, not full
geographic completeness (AK/HI are still in the full-size single-stage maps).

This file intentionally does NOT import from build_choropleth.py or
build_corrected_rankings.py (those are owned by other concurrent work and
must not be modified or relied on); the handful of shared visual constants
(amber colormap, gray conventions, population floor, bin convention) are
copied here instead, matching those files' values exactly for consistency.
"""

from __future__ import annotations

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.colors import BoundaryNorm, LinearSegmentedColormap
from matplotlib.patches import Patch

from breweries.sources import tiger

RAW_RANKINGS_PATH = "data/processed/us_county_shrunken_rankings.parquet"
CORRECTED_RANKINGS_PATH = "data/processed/us_county_corrected_shrunken_rankings.parquet"
OUT_PATH = "data/processed/us_brewery_density_comparison.png"

POPULATION_FLOOR = 50_000

# Same amber colormap / gray conventions as scripts/build_choropleth.py and
# scripts/build_corrected_rankings.py, copied here (not imported) so this file
# has no dependency on those concurrently-owned scripts.
CMAP = LinearSegmentedColormap.from_list(
    "brewery_amber",
    ["#fff5e6", "#ffe0a3", "#ffc266", "#f2932e", "#c96a15", "#8a4008", "#4d2004"],
)
NO_DATA_COLOR = "#e8e8e8"
INSUFFICIENT_POP_COLOR = "#bfbfbf"

# AK, HI, and island territories -- dropped for this CONUS-only comparison figure.
TERRITORY_FIPS = {"02", "15", "72", "78", "60", "66", "69"}


def load_data() -> gpd.GeoDataFrame:
    counties = tiger.load_counties()[["STATEFP", "GEOID", "geometry"]]

    raw = pd.read_parquet(RAW_RANKINGS_PATH)
    raw["county_geoid"] = raw["county_geoid"].str.zfill(5)
    corrected = pd.read_parquet(CORRECTED_RANKINGS_PATH)
    corrected["county_geoid"] = corrected["county_geoid"].str.zfill(5)

    merged = counties.merge(
        raw[["county_geoid", "obdb_rate_per_100k_21plus", "eb_posterior_rate_per_100k",
             "adults_21plus", "county_name", "state_abbr"]],
        left_on="GEOID", right_on="county_geoid", how="left",
    )
    merged = merged.merge(
        corrected[["county_geoid", "eb_posterior_rate_per_100k_corrected"]],
        on="county_geoid", how="left",
    )
    match_rate = merged["eb_posterior_rate_per_100k_corrected"].notna().mean()
    print(f"Counties matched to rate data: {match_rate:.1%}")
    return merged


def main() -> None:
    gdf = load_data()
    gdf_conus = gdf.to_crs(epsg=5070)  # CONUS Albers Equal Area, same as build_choropleth.py
    conus = gdf_conus[~gdf_conus["STATEFP"].isin(TERRITORY_FIPS)].copy()
    conus["_below_floor"] = conus["adults_21plus"] < POPULATION_FLOOR

    panels = [
        ("obdb_rate_per_100k_21plus", "1. Raw Observed Rate",
         "No shrinkage -- raw OBDB count / adults 21+, noisy"),
        ("eb_posterior_rate_per_100k", "2. Empirical-Bayes Shrunken Rate",
         "Partial pooling toward national mean damps small-county noise"),
        ("eb_posterior_rate_per_100k_corrected", "3. Capture-Rate-Corrected + Shrunken Rate",
         "Additionally adjusts for OBDB's state-varying undercount"),
    ]

    # JUDGMENT CALL: shared bins are computed from panel 3 (corrected)'s
    # POPULATION-FLOORED range, not the raw unfiltered range of any panel.
    # Two reasons:
    #   (a) Sub-floor counties are grayed out (not colored) in all three
    #       panels anyway, so their values shouldn't drive the color scale.
    #   (b) The raw rate's extreme outliers (national max ~317 per 100k) come
    #       entirely from population < 50k counties -- a handful of breweries
    #       in a tiny population produces an enormous per-capita rate. Basing
    #       the scale on the *unfloored* raw range would blow out the shared
    #       scale and make panels 2-3 look artificially flat by comparison,
    #       which is exactly the failure mode the task called out. Using the
    #       floored range instead keeps the scale anchored to counties that
    #       are actually colored, and the corrected panel's floored max
    #       (~23/100k) comfortably covers the other two panels' floored
    #       maxima (~20 and ~17 respectively) with no clipping in practice.
    corrected_floored = conus.loc[~conus["_below_floor"], "eb_posterior_rate_per_100k_corrected"].dropna()
    scale_max = corrected_floored.max()
    bins = [0, 1, 3, 6, 10, 15, scale_max + 1]
    labels = ["0-1", "1-3", "3-6", "6-10", "10-15", f"15-{scale_max:.0f}"]
    norm = BoundaryNorm(bins, CMAP.N)

    # Sanity check: confirm the shared scale doesn't silently clip a meaningful
    # number of floored counties in any panel (any that fall above scale_max
    # still render -- BoundaryNorm places above-range values in the top bin --
    # this just flags if that's happening non-trivially).
    for col, _, _ in panels:
        floored_vals = conus.loc[~conus["_below_floor"], col].dropna()
        n_clipped = int((floored_vals > scale_max).sum())
        if n_clipped:
            print(f"NOTE: {col} has {n_clipped} population-floored counties above the shared "
                  f"scale max ({scale_max:.1f}) -- rendered in the top bin's color, not dropped.")

    fig, axes = plt.subplots(1, 3, figsize=(22, 8.5), facecolor="white")

    for ax, (col, title, subtitle) in zip(axes, panels):
        conus["_value"] = conus[col].where(~conus["_below_floor"])
        conus.plot(ax=ax, column="_value", cmap=CMAP, norm=norm,
                   edgecolor="#888888", linewidth=0.15, missing_kwds={"color": NO_DATA_COLOR})
        below = conus[conus["_below_floor"]]
        if len(below):
            below.plot(ax=ax, color=INSUFFICIENT_POP_COLOR, edgecolor="#888888", linewidth=0.15)
        ax.set_axis_off()
        ax.set_title(f"{title}\n{subtitle}", fontsize=11.5, fontweight="bold", pad=8)

    fig.suptitle("US Brewery Density: Raw Rate to Shrinkage to Capture-Rate Correction",
                 fontsize=16, fontweight="bold", y=0.985)
    fig.text(0.5, 0.925,
             "Same underlying county-level estimate at three stages of one analysis pipeline, on a "
             "shared color scale for direct comparison (CONUS only -- AK/HI insets omitted here for compactness)",
             ha="center", fontsize=10.5, color="#333333")

    legend_elems = [Patch(facecolor=CMAP(norm((bins[i] + bins[i + 1]) / 2)), edgecolor="#888888",
                           label=labels[i]) for i in range(len(labels))]
    legend_elems.append(Patch(facecolor=INSUFFICIENT_POP_COLOR, edgecolor="#888888",
                               label=f"< {POPULATION_FLOOR:,} adults 21+"))
    legend_elems.append(Patch(facecolor=NO_DATA_COLOR, edgecolor="#888888", label="No data"))
    fig.legend(handles=legend_elems, loc="lower center", ncol=len(legend_elems),
              bbox_to_anchor=(0.5, 0.075), title="Breweries per 100k adults 21+ (shared scale, all three panels)",
              fontsize=9, title_fontsize=10, frameon=False)

    fig.text(0.5, 0.01,
             "Sources: Open Brewery DB, Census ACS 5-year (2020-2024). Panel 1: raw rate, no adjustment. "
             "Panel 2: empirical Bayes Poisson-Gamma shrinkage (partial pooling toward national mean, "
             "calibrated on 13-state licensee data). Panel 3: additionally applies the capture-rate "
             "correction model (calibrated states use their empirical capture rate; other states use the "
             "pooled WLS-regression rate + density adjustment, capped at 1.0) before the same shrinkage. "
             "Counties under 50,000 adults 21+ are grayed out in all three panels -- shrinkage reduces but "
             "doesn't eliminate small-county noise. Color scale bins are shared across all three panels and "
             "set from panel 3's population-floored range (the widest of the three) for direct visual "
             "comparability -- see build_map_comparison.py module docstring for the judgment call on why "
             "the floored, not raw, range was used to set the scale.",
             ha="center", fontsize=7.5, color="#555555", wrap=True)

    fig.subplots_adjust(left=0.02, right=0.98, top=0.86, bottom=0.15, wspace=0.03)
    fig.savefig(OUT_PATH, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
