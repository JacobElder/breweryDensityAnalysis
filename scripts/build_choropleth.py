"""National county-level choropleth(s) of brewery density, using the project's
adopted headline model (covariates + state FE + BYM2 spatial random effect,
see fit_combined_spatial_covariate_model.py and methods memo Section 15),
styled like a standard county choropleth: binned color scale, CONUS main map
with AK/HI insets, labeled major cities from the face-validity list.

Produces two versions:
- us_brewery_density_choropleth.png: every county colored by its rate.
- us_brewery_density_choropleth_floored.png: counties below POPULATION_FLOOR
  shown in a distinct "insufficient population" gray rather than colored —
  the model's shrinkage/spatial-smoothing reduces but does not eliminate
  small-county noise (a county can still land in the darkest bin off a
  handful of breweries), so this floored version is the more conservative
  one to read as a "where is density high" map.

Labeling is collision-aware (src/breweries/map_labels.py), not a fixed list:
the original face-validity cities are placed first as priority anchors, then
the highest-rate remaining counties (population-floored) are added as space
allows, skipping any that would land within ~80km of an anchor already placed
(to avoid e.g. both "Boulder, CO" and its own county's auto-label competing
for the same spot) or that would visually collide with another label.
"""

from __future__ import annotations

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.colors import BoundaryNorm, LinearSegmentedColormap
from matplotlib.patches import Patch

from breweries.map_labels import LabelCandidate, place_labels
from breweries.sources import tiger

# The project's headline county model, as of this round: covariates + state
# FE + a BYM2 spatial random effect (scripts/fit_combined_spatial_covariate_model.py),
# adopted after it beat Model A (flat-mean shrinkage), Model B (covariates
# alone), and the CAR-only spatial model on held-out log-likelihood -- see
# docs/methods_memo.md Section 15. Model A's plain shrunken rate is still
# computed and shipped in this same file (`eb_posterior_rate_per_100k`) for
# comparison, just no longer the default map.
RANKINGS_PATH = "data/processed/us_county_combined_model_rankings.parquet"
VALUE_COL = "combined_posterior_rate_per_100k"
POPULATION_FLOOR = 50_000
MAX_AUTO_LABELS = 22
ANCHOR_EXCLUSION_RADIUS_M = 80_000  # ~50 miles; skip an auto-label this close to a placed anchor

# Face-validity cities from the project handoff, plus a few discovered during
# calibration (Boulder, Grand Traverse) — placed first, so they always win any
# contested space against auto-generated labels.
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


def load_county_geodata() -> gpd.GeoDataFrame:
    # NAMELSAD (not the bare NAME) is used for auto-generated labels: Virginia's
    # independent cities share a bare county name with a same-named county
    # (e.g. both "Richmond city" and "Richmond County" have NAME="Richmond"),
    # so labeling off NAME risks mislabeling a high-rate independent city as
    # the wrong, much-lower-rate county. NAMELSAD disambiguates correctly
    # everywhere (also handles Louisiana's "X Parish" naming).
    counties = tiger.load_counties()[["STATEFP", "GEOID", "NAMELSAD", "geometry"]]

    rankings = pd.read_parquet(RANKINGS_PATH)
    rankings["county_geoid"] = rankings["county_geoid"].str.zfill(5)

    merged = counties.merge(
        rankings[["county_geoid", VALUE_COL, "adults_21plus", "state_abbr"]],
        left_on="GEOID", right_on="county_geoid", how="left",
    )
    match_rate = merged[VALUE_COL].notna().mean()
    print(f"Counties matched to rate data: {match_rate:.1%}")
    return merged


def build_auto_label_candidates(
    conus_albers: gpd.GeoDataFrame, anchor_points_albers: list[tuple[float, float]],
    value_col: str = VALUE_COL,
) -> list[LabelCandidate]:
    """Top-rate counties (population-floored), as label candidates in Albers
    meters, excluding any within ANCHOR_EXCLUSION_RADIUS_M of a placed anchor.
    """
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


def build_map(gdf: gpd.GeoDataFrame, out_path: str, floor: int | None,
               value_col: str = VALUE_COL, title_prefix: str = "US Brewery Density by County",
               source_note: str | None = None) -> None:
    """Render the CONUS+AK+HI choropleth. If floor is set, counties with fewer
    adults_21plus than floor are drawn in a distinct gray instead of colored.
    """
    gdf = gdf.copy()
    if floor is not None:
        gdf["_below_floor"] = gdf["adults_21plus"] < floor
        gdf["_value"] = gdf[value_col].where(~gdf["_below_floor"])
    else:
        gdf["_below_floor"] = False
        gdf["_value"] = gdf[value_col]

    gdf_conus = gdf.to_crs(epsg=5070)  # CONUS Albers Equal Area
    territory_fips = {"02", "15", "72", "78", "60", "66", "69"}  # AK, HI, and island territories
    conus = gdf_conus[~gdf_conus["STATEFP"].isin(territory_fips)]
    alaska = gdf[gdf["STATEFP"] == "02"].to_crs(epsg=3338)
    hawaii = gdf[gdf["STATEFP"] == "15"].to_crs(epsg=3563)

    values = gdf[value_col].dropna()
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

    title = title_prefix
    subtitle = ("Combined model: covariates + state fixed effects + a BYM2 spatial random effect, "
                "per 100,000 adults 21+")
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
                        title="Breweries per 100k\nadults 21+", fontsize=9, title_fontsize=10, frameon=False)

    # Reserve the legend's own footprint so auto-labels don't get placed on top of it.
    fig.canvas.draw()
    reserved = [legend.get_window_extent(renderer=fig.canvas.get_renderer())]

    # Anchor cities first (always win contested space over auto-labels).
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

    # Then data-driven top-rate counties, excluding anything too close to an anchor.
    anchor_points = [(geom.x, geom.y) for geom in cities_gdf.geometry]
    auto_candidates = build_auto_label_candidates(conus, anchor_points, value_col)
    fig.canvas.draw()
    reserved_after_anchors = reserved + [
        t.get_window_extent(renderer=fig.canvas.get_renderer())
        for t in ax.texts
    ]
    n_auto = place_labels(fig, ax, auto_candidates, max_labels=MAX_AUTO_LABELS,
                           reserved_boxes=reserved_after_anchors)
    print(f"  Labels placed: {n_anchors} anchors + {n_auto} auto (of {len(auto_candidates)} candidates)")

    fig.text(0.5, 0.01,
              source_note or
              "Sources: Open Brewery DB, Census ACS 5-year (2020-2024). County rate is the "
              "project's adopted headline model — income, age, college share, tourism, "
              "population growth, unemployment, and rent covariates plus state fixed effects "
              "and a BYM2 spatial random effect (neighboring counties inform each other's "
              "estimate), validated by held-out log-likelihood against three simpler "
              "alternatives (see methods memo Section 15). OBDB undercounts true brewery count "
              "by an amount that varies by state (see methods memo Section 5) — this map is "
              "uncorrected for that gap.",
              ha="center", fontsize=6.8, color="#555555", wrap=True)

    fig.savefig(out_path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Wrote {out_path}")


def main() -> None:
    gdf = load_county_geodata()
    build_map(gdf, "data/processed/us_brewery_density_choropleth.png", floor=None)
    build_map(gdf, "data/processed/us_brewery_density_choropleth_floored.png", floor=POPULATION_FLOOR)


if __name__ == "__main__":
    main()
