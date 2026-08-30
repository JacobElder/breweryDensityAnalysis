"""National county-level choropleth(s) of brewery density (shrunken posterior rate),
styled like a standard county choropleth: binned color scale, CONUS main map with
AK/HI insets, labeled major cities from the face-validity list.

Produces two versions:
- us_brewery_density_choropleth.png: every county colored by its shrunken rate.
- us_brewery_density_choropleth_floored.png: counties below POPULATION_FLOOR
  shown in a distinct "insufficient population" gray rather than colored —
  empirical Bayes shrinkage reduces but does not eliminate small-county noise
  (a county can still land in the darkest bin off a handful of breweries), so
  this floored version is the more conservative one to read as a "where is
  density high" map.
"""

from __future__ import annotations

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.colors import BoundaryNorm, LinearSegmentedColormap
from matplotlib.patches import Patch

from breweries.sources import tiger

RANKINGS_PATH = "data/processed/us_county_shrunken_rankings.parquet"
POPULATION_FLOOR = 50_000

# Face-validity cities from the project handoff, plus a few discovered during
# calibration (Boulder, Grand Traverse) — labeled to anchor the reader.
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
    counties = tiger.load_counties()[["STATEFP", "GEOID", "geometry"]]

    rankings = pd.read_parquet(RANKINGS_PATH)
    rankings["county_geoid"] = rankings["county_geoid"].str.zfill(5)

    merged = counties.merge(
        rankings[["county_geoid", "eb_posterior_rate_per_100k", "adults_21plus"]],
        left_on="GEOID", right_on="county_geoid", how="left",
    )
    match_rate = merged["eb_posterior_rate_per_100k"].notna().mean()
    print(f"Counties matched to rate data: {match_rate:.1%}")
    return merged


def build_map(gdf: gpd.GeoDataFrame, out_path: str, floor: int | None) -> None:
    """Render the CONUS+AK+HI choropleth. If floor is set, counties with fewer
    adults_21plus than floor are drawn in a distinct gray instead of colored.
    """
    gdf = gdf.copy()
    if floor is not None:
        gdf["_below_floor"] = gdf["adults_21plus"] < floor
        gdf["_value"] = gdf["eb_posterior_rate_per_100k"].where(~gdf["_below_floor"])
    else:
        gdf["_below_floor"] = False
        gdf["_value"] = gdf["eb_posterior_rate_per_100k"]

    gdf_conus = gdf.to_crs(epsg=5070)  # CONUS Albers Equal Area
    territory_fips = {"02", "15", "72", "78", "60", "66", "69"}  # AK, HI, and island territories
    conus = gdf_conus[~gdf_conus["STATEFP"].isin(territory_fips)]
    alaska = gdf[gdf["STATEFP"] == "02"].to_crs(epsg=3338)
    hawaii = gdf[gdf["STATEFP"] == "15"].to_crs(epsg=3563)

    values = gdf["eb_posterior_rate_per_100k"].dropna()
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

    title = "US Brewery Density by County"
    subtitle = "Shrunken posterior rate per 100,000 adults 21+ (empirical Bayes, partial pooling toward national mean)"
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
    ax.legend(handles=legend_elems, loc="lower left", bbox_to_anchor=(0.33, -0.02),
              title="Breweries per 100k\nadults 21+", fontsize=9, title_fontsize=10, frameon=False)

    cities_gdf = gpd.GeoDataFrame(
        {"label": [c[0] for c in LABEL_CITIES]},
        geometry=gpd.points_from_xy([c[1] for c in LABEL_CITIES], [c[2] for c in LABEL_CITIES]),
        crs="EPSG:4326",
    ).to_crs(epsg=5070)
    for label, geom in zip(cities_gdf["label"], cities_gdf.geometry):
        ax.plot(geom.x, geom.y, marker="o", markersize=4, color="black", zorder=5)
        ax.annotate(label, (geom.x, geom.y), xytext=(5, 5), textcoords="offset points",
                    fontsize=8, fontweight="bold", color="black", zorder=6)

    fig.text(0.5, 0.01,
              "Sources: Open Brewery DB, Census ACS 5-year (2020-2024), empirical Bayes shrinkage "
              "calibrated on NC/MI/CO/OR state licensee data. OBDB undercounts true brewery count "
              "by an estimated 7-38% depending on state (see methods memo) — this map is "
              "uncorrected for that gap.",
              ha="center", fontsize=7.5, color="#555555", wrap=True)

    fig.savefig(out_path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Wrote {out_path}")


def main() -> None:
    gdf = load_county_geodata()
    build_map(gdf, "data/processed/us_brewery_density_choropleth.png", floor=None)
    build_map(gdf, "data/processed/us_brewery_density_choropleth_floored.png", floor=POPULATION_FLOOR)


if __name__ == "__main__":
    main()
