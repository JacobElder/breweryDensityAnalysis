"""National county-level choropleth(s) of brewery density, using the project's
adopted headline model (covariates + state FE + BYM2 spatial random effect,
see fit_combined_spatial_covariate_model.py and methods memo Section 15),
styled like a standard county choropleth: binned color scale, CONUS main map
with AK/HI insets, labeled major cities from the face-validity list.

Produces three versions:

- us_brewery_density_choropleth_uncertainty.png  <- THE RECOMMENDED ONE.
  Every county coloured by its rate, with the colour faded toward the page
  where the model's own 95% interval is wide. Small, data-poor counties wash
  out gradually instead of being replaced by a flat grey, so the map never
  shows a county more confidently than the data supports and never hides one
  entirely.
- us_brewery_density_choropleth.png: every county coloured by its rate, no
  reliability encoding. Honest about coverage, silent about uncertainty.
- us_brewery_density_choropleth_floored.png: counties below POPULATION_FLOOR
  drawn as white-with-hatching rather than coloured.

WHY THE FLOORED VERSION IS NO LONGER THE DEFAULT
------------------------------------------------
The population floor is a real guard against a real problem. 1,916 of 3,222
counties (59%) have ZERO observed breweries, and without a floor 238 of them
paint in the "3-6 per 100k" bin or darker purely from the model's prior --
Jackson County CO (0 breweries, 1,121 adults 21+) lands at 30.6 per 100k, 11th
highest in the country. So "just show the raw data" is not
available; the model has to say something about counties where nothing was
observed, and a floor is one way to decline to show it.

But a hard floor is the wrong instrument, for two reasons:

1. COVERAGE. The floor greys 2,405 of 3,222 counties -- 74.6% of the map --
   to suppress noise affecting 16.0% of breweries and 15.5% of the adult
   population. Three-quarters of the country is withheld to protect a sixth
   of the data.

2. RENDERING. The old "insufficient population" grey (#bfbfbf) has relative
   luminance 0.521. The "3-6 per 100k" bin has luminance 0.516. They were
   optically identical, so the single most common colour on the map was a
   non-value that read as a mid-scale value -- and at Reddit thumbnail size
   that IS the map. Readers reported exactly this ("I have no idea what we're
   measuring"). The grey is now white-with-hatching, which sits off the
   lightness ramp entirely and cannot be misread as a quantity.

The uncertainty version replaces the binary in/out decision with the
continuous thing the floor was standing in for. Population is only a proxy
for "how much do we know about this county"; the posterior interval is the
actual answer, and it also catches large counties with thin coverage.

OTHER LEGEND FIXES APPLIED HERE
-------------------------------
- Bins are computed from the values actually DRAWN, not from the full column.
  Previously the floored map's top bin was labelled "15-77" while the highest
  visible county was Tompkins NY at 16.1, because the bin edges were taken
  from the unfloored data -- the legend advertised a range no visible county
  occupied, and exactly one county sat in that bin.
- The "No data" swatch is only drawn if some county in frame actually has no
  data. It applied to zero counties in the published map while occupying a
  second grey in the legend at luminance 0.807, between the two lightest data
  bins.
- The AK and HI insets are labelled with the model that actually produced
  them. Those 35 counties have no Queen-contiguity neighbours and so fall back
  to Model A (flat-mean shrinkage), not the BYM2 model named in the caption.
  Several sit pinned at the prior mean (Denali 4.96, Haines 4.96,
  Hoonah-Angoon 4.93 per 100k) -- an artifact of the fallback, not a finding.

Labeling is collision-aware (src/breweries/map_labels.py), not a fixed list:
the original face-validity cities are placed first as priority anchors, then
the highest-rate remaining counties (population-floored) are added as space
allows, skipping any that would land within ~80km of an anchor already placed
(to avoid e.g. both "Boulder, CO" and its own county's auto-label competing
for the same spot) or that would visually collide with another label. Each
label is tied to its county by a leader line, because a label's TEXT can
extend far from its own dot in a dense region -- readers misattributed
"Hampshire County, MA" and "Whatcom County, WA" for exactly this reason.
"""

from __future__ import annotations

import os

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import BoundaryNorm, LinearSegmentedColormap, to_rgb
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
CI_LOW_COL = "combined_ci_low_per_100k"
CI_HIGH_COL = "combined_ci_high_per_100k"
POPULATION_FLOOR = 50_000
MAX_AUTO_LABELS = 22
ANCHOR_EXCLUSION_RADIUS_M = 80_000  # ~50 miles; skip an auto-label this close to a placed anchor

# Reliability ramp for the uncertainty map, in units of the posterior
# interval's multiplicative width (ci_high / ci_low). A county whose 95%
# interval spans a factor of RELIABLE_CI_RATIO or less is drawn at full
# strength; one spanning UNRELIABLE_CI_RATIO or more fades almost entirely to
# the page; between them the fade is linear in log width. The defaults bracket
# the observed distribution -- counties with >=20 observed breweries sit near
# 2.6x, counties with none near 5.0x.
RELIABLE_CI_RATIO = 2.5
UNRELIABLE_CI_RATIO = 8.0
MIN_ALPHA = 0.10  # never fully invisible: "we don't know" still has to be locatable

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
# State outlines, drawn over the county fill. County edges alone are a uniform
# hairline mesh, which is what makes a dense-county region like the Midwest
# read as noise -- a reader has no way to find a state they know and orient
# from it. Requested directly in the feedback ("make the borders between
# states darker"); costs one dissolve.
STATE_EDGE_COLOR = "#3a3a3a"
STATE_EDGE_WIDTH = 0.55
# Below-floor counties are drawn as the page colour with a hatch, NOT as a
# grey: any grey competes on the same lightness ramp the data uses. See the
# module docstring for the luminance collision this replaces.
BELOW_FLOOR_FACE = "#ffffff"
BELOW_FLOOR_HATCH = "///"
BELOW_FLOOR_EDGE = "#b0b0b0"
PAGE_COLOR = "#ffffff"


def _assert_basis_consistent() -> None:
    """Refuse to render if the model output is on a different brewery-count
    basis than the analysis dataset.

    The two can diverge by one step: the capture basis and the analysis
    dataset were moved to the OBDB-union-OSM count, but the model refit that
    would follow was blocked (nine OOM kills; see methods memo 18.20). In that
    window `build_choropleth.py` reads OBDB-basis rankings while
    `build_corrected_rankings.py` reads union-basis corrected counts, so
    regenerating both would ship two maps built on different numerators with
    nothing on either saying so. Better to stop than to publish that.
    """
    analysis_path = "data/processed/us_county_analysis.parquet"
    if not os.path.exists(analysis_path) or not os.path.exists(RANKINGS_PATH):
        return
    from breweries.capture_rate_model import CAPTURE_BASIS

    a = pd.read_parquet(analysis_path)
    r = pd.read_parquet(RANKINGS_PATH)
    # Compare the ACTIVE numerator, the one CAPTURE_BASIS selects -- not
    # union_count unconditionally. Comparing the wrong column made this guard
    # fire on a state that was in fact consistent.
    numerator_col = "union_count" if CAPTURE_BASIS == "union" else "obdb_count"
    if numerator_col not in a.columns:
        return
    analysis_total = int(a[numerator_col].sum())
    model_total = int(r["obdb_count"].sum())
    if analysis_total != model_total:
        raise SystemExit(
            "ABORT: brewery-count basis mismatch.\n"
            f"  us_county_analysis.parquet ({numerator_col}) : {analysis_total:,}\n"
            f"  model rankings (as fitted)                 : {model_total:,}\n"
            "The model has not been refit since the numerator changed. Re-run\n"
            "scripts/fit_combined_spatial_covariate_model.py --production-only\n"
            "before regenerating maps, or set CAPTURE_BASIS='obdb' and rebuild\n"
            "the analysis dataset to go back. See methods memo 18.13 and 18.20."
        )


def load_county_geodata() -> gpd.GeoDataFrame:
    _assert_basis_consistent()
    # CARTOGRAPHIC BOUNDARY geometry, not TIGER/Line: TIGER carries legal
    # boundaries, which extend county polygons across open water, filling the
    # Great Lakes and Chesapeake Bay with solid county colour (Keweenaw County
    # MI is 91% water by TIGER area, Leelanau 86%). CB files are clipped to
    # the shoreline. Display only -- every spatial join elsewhere in the
    # project still uses tiger.load_counties(). See breweries.sources.tiger.
    #
    # NAMELSAD (not the bare NAME) is used for auto-generated labels: Virginia's
    # independent cities share a bare county name with a same-named county
    # (e.g. both "Richmond city" and "Richmond County" have NAME="Richmond"),
    # so labeling off NAME risks mislabeling a high-rate independent city as
    # the wrong, much-lower-rate county. NAMELSAD disambiguates correctly
    # everywhere (also handles Louisiana's "X Parish" naming).
    counties = tiger.load_cb_counties()[["STATEFP", "GEOID", "NAMELSAD", "geometry"]]

    rankings = pd.read_parquet(RANKINGS_PATH)
    rankings["county_geoid"] = rankings["county_geoid"].str.zfill(5)

    cols = ["county_geoid", VALUE_COL, CI_LOW_COL, CI_HIGH_COL, "adults_21plus",
            "state_abbr", "spatial_smoothing_applied", "obdb_count"]
    merged = counties.merge(rankings[cols], left_on="GEOID", right_on="county_geoid", how="left")
    match_rate = merged[VALUE_COL].notna().mean()
    print(f"Counties matched to rate data: {match_rate:.1%}")
    return merged


def compute_bins(values: pd.Series) -> tuple[list[float], list[str]]:
    """Bin edges and labels from the values that will actually be DRAWN.

    Passing the full column here (rather than the visible subset) is what
    produced the published map's phantom "15-77" top bin: the maximum came
    from a county the floor had already removed from the map.
    """
    vmax = float(values.max())
    edges = [0, 1, 3, 6, 10, 15, max(vmax, 15.0) + 1e-9]
    labels = ["0-1", "1-3", "3-6", "6-10", "10-15", f"15-{vmax:.0f}"]
    # Drop any trailing bin no drawn county occupies, so the legend can't
    # advertise a range that isn't on the map.
    while len(labels) > 1 and not ((values >= edges[len(labels) - 1]).any()):
        edges.pop(-2)
        labels.pop()
        labels[-1] = f"{edges[-2]:.0f}-{vmax:.0f}"
    return edges, labels


def reliability(gdf: gpd.GeoDataFrame) -> np.ndarray:
    """Per-county drawing strength in [MIN_ALPHA, 1] from posterior interval width.

    Uses the multiplicative width of the model's own 95% interval, which is
    the direct measure of how much the estimate is data versus prior. A
    population floor is only a proxy for this -- and a lossy one, since it
    misses large counties with thin coverage and excludes small counties whose
    estimate is actually well-pinned.
    """
    lo = gdf[CI_LOW_COL].to_numpy(dtype=float)
    hi = gdf[CI_HIGH_COL].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(lo > 0, hi / lo, np.inf)
    log_ratio = np.log(np.clip(ratio, RELIABLE_CI_RATIO, UNRELIABLE_CI_RATIO))
    span = np.log(UNRELIABLE_CI_RATIO) - np.log(RELIABLE_CI_RATIO)
    r = 1.0 - (log_ratio - np.log(RELIABLE_CI_RATIO)) / span
    r = MIN_ALPHA + (1.0 - MIN_ALPHA) * r
    return np.where(np.isfinite(r), r, MIN_ALPHA)


def fade_to_page(colors: np.ndarray, strength: np.ndarray) -> np.ndarray:
    """Blend RGBA colours toward the page colour by `strength` in [0,1].

    Blending toward the page rather than setting alpha keeps the output
    independent of what is drawn underneath, so the PNG looks the same however
    it is composited.
    """
    page = np.array(to_rgb(PAGE_COLOR))
    out = colors.copy()
    out[:, :3] = page[None, :] + (colors[:, :3] - page[None, :]) * strength[:, None]
    return out


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
               value_col: str = VALUE_COL,
               title_prefix: str = "Breweries per 100,000 Adults 21+, by US County",
               source_note: str | None = None, encode_uncertainty: bool = False) -> None:
    """Render the CONUS+AK+HI choropleth.

    floor: if set, counties with fewer adults_21plus than floor are drawn as
        hatched white instead of coloured.
    encode_uncertainty: if True, each county's colour is faded toward the page
        in proportion to its posterior interval width (see `reliability`).
        Mutually exclusive with `floor` in practice -- the whole point is that
        it replaces the binary decision.
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

    # Bin edges from what is DRAWN, not from the whole column.
    drawn_values = pd.concat([conus["_value"], alaska["_value"], hawaii["_value"]]).dropna()
    bins, labels = compute_bins(drawn_values)
    norm = BoundaryNorm(bins, CMAP.N)

    if encode_uncertainty:
        gdf_conus["_strength"] = reliability(gdf_conus)
        alaska["_strength"] = reliability(alaska)
        hawaii["_strength"] = reliability(hawaii)
        conus = gdf_conus[~gdf_conus["STATEFP"].isin(territory_fips)]

    def draw(ax, sub):
        if encode_uncertainty:
            vals = sub["_value"].to_numpy(dtype=float)
            colors = CMAP(norm(np.nan_to_num(vals, nan=0.0)))
            colors = fade_to_page(colors, sub["_strength"].to_numpy(dtype=float))
            colors[np.isnan(vals)] = list(to_rgb(NO_DATA_COLOR)) + [1.0]
            sub.plot(ax=ax, color=colors, edgecolor="#999999", linewidth=0.15)
        else:
            sub.plot(ax=ax, column="_value", cmap=CMAP, norm=norm,
                      edgecolor="#888888", linewidth=0.2,
                      missing_kwds={"color": NO_DATA_COLOR})
        below = sub[sub["_below_floor"]]
        if len(below):
            below.plot(ax=ax, color=BELOW_FLOOR_FACE, edgecolor=BELOW_FLOOR_EDGE,
                        linewidth=0.2, hatch=BELOW_FLOOR_HATCH)
        # State outlines last, so they sit above every county fill and hatch.
        if len(sub):
            sub.dissolve(by="STATEFP").boundary.plot(
                ax=ax, color=STATE_EDGE_COLOR, linewidth=STATE_EDGE_WIDTH, zorder=4)
        ax.set_axis_off()

    fig = plt.figure(figsize=(16, 10), facecolor=PAGE_COLOR)
    ax = fig.add_axes((0.02, 0.08, 0.96, 0.86))
    ax.set_facecolor(PAGE_COLOR)
    draw(ax, conus)

    title = title_prefix
    subtitle = "Model-estimated breweries per 100,000 adults aged 21 and over"
    if floor is not None:
        title += " (population-floored)"
        subtitle = (f"Counties with fewer than {floor:,} adults aged 21+ are hatched rather than "
                    "coloured, because their rates are too uncertain to map")
    elif encode_uncertainty:
        title += " (faded where uncertain)"
        subtitle = ("Model-estimated breweries per 100,000 adults aged 21 and over. Counties are "
                    "shown fainter where the estimate is more uncertain")
    ax.set_title(title, fontsize=17, fontweight="bold", pad=12)

    # AK/HI carry Model A's flat-mean shrunken rate, NOT the BYM2 model the
    # title and caption describe: they have no Queen-contiguity neighbours, so
    # the spatial term cannot be fit for them. Say so on the inset rather than
    # letting them pass as the same estimate.
    ak_fallback = (~alaska["spatial_smoothing_applied"].fillna(True)).any()
    hi_fallback = (~hawaii["spatial_smoothing_applied"].fillna(True)).any()

    ax_ak = fig.add_axes((0.02, 0.05, 0.20, 0.22))
    draw(ax_ak, alaska)
    ax_ak.set_title("AK" + ("*" if ak_fallback else ""), fontsize=9)

    ax_hi = fig.add_axes((0.20, 0.05, 0.10, 0.14))
    draw(ax_hi, hawaii)
    ax_hi.set_title("HI" + ("*" if hi_fallback else ""), fontsize=9)

    legend_elems = [Patch(facecolor=CMAP(norm((bins[i] + bins[i + 1]) / 2)), edgecolor="#888888",
                           label=labels[i]) for i in range(len(labels))]
    if floor is not None:
        legend_elems.append(Patch(facecolor=BELOW_FLOOR_FACE, edgecolor=BELOW_FLOOR_EDGE,
                                   hatch=BELOW_FLOOR_HATCH, label=f"< {floor:,} adults 21+"))
    # Only advertise "No data" if some drawn county actually lacks data.
    n_missing = int(pd.concat([conus["_value"], alaska["_value"], hawaii["_value"]]).isna().sum())
    n_missing -= int(gdf["_below_floor"].sum()) if floor is not None else 0
    if n_missing > 0:
        legend_elems.append(Patch(facecolor=NO_DATA_COLOR, edgecolor="#888888", label="No data"))
    # Bottom-right corner of the CONUS axes lands over the Atlantic/Gulf, ocean
    # space with no county polygons -- previously bottom-left near x=0.33 sat
    # almost directly under Texas.
    legend = ax.legend(handles=legend_elems, loc="lower right", bbox_to_anchor=(0.99, 0.01),
                        title="Breweries per 100k\nadults 21+", fontsize=9, title_fontsize=10, frameon=False)

    reserved_extra = []
    if encode_uncertainty:
        reserved_extra.append(_draw_uncertainty_key(fig, ax, bins, norm))

    # Reserve the legend's own footprint so auto-labels don't get placed on top of it.
    fig.canvas.draw()
    reserved = [legend.get_window_extent(renderer=fig.canvas.get_renderer())] + reserved_extra

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

    # Plain-language footnote. The previous wording ("fall back to the
    # flat-mean shrinkage model, not the spatial model") described the cause
    # accurately and told a general reader nothing they could act on. What
    # matters to them is that these estimates are not comparable with the rest.
    footnote = ("  *Alaska and Hawaii have no neighbouring counties on the mainland, so their "
                "estimates are calculated differently and are less reliable. "
                if (ak_fallback or hi_fallback) else "")
    # Same principle as the count/rate captions: the one thing a reader needs
    # in order to read the map correctly, plus sources. The model
    # specification, its validation and its known biases are in
    # docs/methods_memo.md, not squeezed into a footer nobody finishes.
    caption = subtitle + ". " + footnote + (source_note or
              "Rates are model estimates, adjusted for population, income, age, education, "
              "tourism and neighbouring counties, so they differ from a county's raw brewery "
              "count. Listings are incomplete by an estimated 7-54% depending on the state and "
              "this map is not corrected for that.\n"
              "Sources: Open Brewery DB, US Census ACS 2020-2024.")
    fig.text(0.5, 0.01, caption, ha="center", fontsize=7.2, color="#555555", wrap=True)

    fig.savefig(out_path, dpi=180, bbox_inches="tight", facecolor=PAGE_COLOR)
    plt.close(fig)
    print(f"Wrote {out_path}")


def _draw_uncertainty_key(fig, ax, bins, norm):
    """Small 2-D key showing that colour fades as the posterior interval widens.

    Returns the key's pixel bbox so label placement can avoid it.
    """
    key_ax = fig.add_axes((0.735, 0.26, 0.13, 0.075))
    mid_bins = [(bins[i] + bins[i + 1]) / 2 for i in range(len(bins) - 1)]
    strengths = [1.0, 0.55, MIN_ALPHA]
    grid = np.zeros((len(strengths), len(mid_bins), 4))
    for r, s in enumerate(strengths):
        row = CMAP(norm(np.array(mid_bins)))
        grid[r] = fade_to_page(row, np.full(len(mid_bins), s))
    key_ax.imshow(grid, aspect="auto", interpolation="nearest")
    key_ax.set_xticks([])
    key_ax.set_yticks([0, len(strengths) - 1])
    key_ax.set_yticklabels(["narrow", "wide"], fontsize=6.5)
    key_ax.set_ylabel("95% interval", fontsize=6.5, labelpad=2)
    key_ax.set_xlabel("rate →", fontsize=6.5, labelpad=2)
    key_ax.set_title("Confidence", fontsize=7.5, pad=3)
    for spine in key_ax.spines.values():
        spine.set_linewidth(0.4)
        spine.set_color("#888888")
    key_ax.tick_params(length=0, pad=1)
    fig.canvas.draw()
    return key_ax.get_window_extent(renderer=fig.canvas.get_renderer())


def attach_union_count(gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, str, str]:
    """Merge the OBDB-union-OSM count in, returning (gdf, count_col, source_label).

    Factored out because BOTH count renderers need it and having the merge live
    inside one of them meant the other silently drew OBDB-only counts -- the
    choropleth reported 6,626 breweries and a 140 maximum while the symbol map
    reported 8,369 and 157, from what was supposed to be the same data.
    """
    union_path = "data/processed/us_county_union_counts.parquet"
    if not os.path.exists(union_path):
        return gdf, "obdb_count", "Open Brewery DB"
    u = pd.read_parquet(union_path)
    gdf = gdf.merge(
        u[["county_geoid", "union_count"]].rename(columns={"county_geoid": "GEOID"}),
        on="GEOID", how="left")
    gdf["union_count"] = gdf["union_count"].fillna(gdf["obdb_count"])
    return gdf, "union_count", "Open Brewery DB union OpenStreetMap"


def build_count_map(gdf: gpd.GeoDataFrame, out_path: str) -> None:
    """Proportional-symbol map of the RAW OBSERVED brewery count per county.

    WHY THIS EXISTS. The single most-repeated substantive complaint about the
    published map was that a per-capita model estimate "gives you little to no
    idea how many actual breweries there are in any given place", and a second
    reader independently asked for total counts rather than a per-capita
    adjustment. Both are right that the rate map cannot answer that question,
    and the project had no output that could: every county-level rendering was
    a modelled rate.

    This is deliberately the RAW count -- no model, no shrinkage, no capture
    correction, no population denominator. It is the "just report the data"
    map, and it belongs next to the rate map precisely because the two
    disagree in an informative way: metros dominate here and vanish on the
    rate map, which is the actual finding rather than an artifact.

    Proportional SYMBOLS, not a choropleth, because a count filled across a
    polygon is read as a density by area -- San Bernardino County would
    outweigh Manhattan on visual weight alone. Symbol area (not radius) is
    proportional to count, which is the encoding people actually decode.
    """
    # Prefer the OBDB-union-OSM count where it exists. Against the 18 state
    # registries with trustworthy ground truth, OBDB alone runs a median 34.6%
    # below truth; the union lifts national coverage from 69% to 87% of the
    # Brewers Association figure and takes the median calibrated-state capture
    # rate from 0.654 to 0.871. See scripts/build_union_county_counts.py.
    gdf, count_col, source_note = attach_union_count(gdf)
    gdf = gdf[gdf[count_col].notna()].copy()
    gdf_conus = gdf.to_crs(epsg=5070)
    territory_fips = {"02", "15", "72", "78", "60", "66", "69"}
    conus = gdf_conus[~gdf_conus["STATEFP"].isin(territory_fips)]
    alaska = gdf[gdf["STATEFP"] == "02"].to_crs(epsg=3338)
    hawaii = gdf[gdf["STATEFP"] == "15"].to_crs(epsg=3563)

    max_count = float(gdf[count_col].max())
    # Area-proportional: matplotlib's `s` IS area in points^2, so scale linearly.
    max_area = 420.0

    def draw(ax, sub):
        sub.boundary.plot(ax=ax, color="#d8d8d8", linewidth=0.12, zorder=1)
        sub.dissolve(by="STATEFP").boundary.plot(
            ax=ax, color=STATE_EDGE_COLOR, linewidth=STATE_EDGE_WIDTH, zorder=2)
        pts = sub[sub[count_col] > 0]
        if len(pts):
            cent = pts.geometry.representative_point()
            ax.scatter(cent.x, cent.y, s=pts[count_col] / max_count * max_area,
                        facecolor="#c96a15", edgecolor="#4d2004", linewidth=0.25,
                        alpha=0.75, zorder=3)
        ax.set_axis_off()

    fig = plt.figure(figsize=(16, 10), facecolor=PAGE_COLOR)
    ax = fig.add_axes((0.02, 0.08, 0.96, 0.86))
    ax.set_facecolor(PAGE_COLOR)
    draw(ax, conus)
    # Titles say what the map SHOWS, not how it was built. "(raw count, no
    # model)" meant something to whoever wrote it and nothing to a general
    # reader, who has no reason to know the project has a modelled alternative.
    # The methodology belongs in the caption, where it already is.
    ax.set_title("Breweries per US County",
                  fontsize=17, fontweight="bold", pad=12)

    ax_ak = fig.add_axes((0.02, 0.05, 0.20, 0.22))
    draw(ax_ak, alaska)
    ax_ak.set_title("AK", fontsize=9)
    ax_hi = fig.add_axes((0.20, 0.05, 0.10, 0.14))
    draw(ax_hi, hawaii)
    ax_hi.set_title("HI", fontsize=9)

    # Legend: circles at real counts, area-scaled identically to the map.
    handles, labels = [], []
    for n in (1, 10, 50, int(max_count)):
        handles.append(plt.scatter([], [], s=n / max_count * max_area, facecolor="#c96a15",
                                    edgecolor="#4d2004", linewidth=0.25, alpha=0.75))
        labels.append(f"{n:,}")
    ax.legend(handles, labels, loc="lower right", bbox_to_anchor=(0.99, 0.01),
               title=f"Breweries in county\n({source_note})", labelspacing=1.6,
               borderpad=1.0, frameon=False, fontsize=9, title_fontsize=10, scatterpoints=1)

    total = int(gdf[count_col].sum())
    # Captions are written for a general reader: what the data is, the one
    # caveat that changes how you should read it, and the sources. Everything
    # else -- area bias, zero-county share, boundary provenance, why there is
    # no model -- lives in the README and docs/methods_memo.md. An earlier
    # version ran to four lines opening with "READ WITH CARE", which is a
    # caption written for someone who has already had the argument.
    fig.text(0.5, 0.01,
              f"Brewery listings per county, {total:,} nationally. Circle area is proportional to "
              "the number of breweries. Listings are incomplete by an estimated 7-54% depending on "
              "the state, so treat these as a floor rather than a census.\n"
              "Sources: Open Brewery DB, OpenStreetMap, US Census.",
              ha="center", fontsize=7.2, color="#555555", wrap=True)

    fig.savefig(out_path, dpi=180, bbox_inches="tight", facecolor=PAGE_COLOR)
    plt.close(fig)
    print(f"Wrote {out_path}")


# Bin edges for the COUNT choropleth. Counts are far more skewed than rates --
# 55% of counties have none and the top 11 hold 50+ each -- so equal-width bins
# would put almost everything in one class. These are roughly log-spaced, with
# zero given its own class because "no breweries listed" is a different
# statement from "few".
COUNT_BINS = [0, 1, 2, 3, 5, 10, 20, 50]
COUNT_ZERO_COLOR = "#f7f7f7"
MAX_COUNT_LABELS = 20


def build_count_choropleth(gdf: gpd.GeoDataFrame, out_path: str) -> None:
    """Choropleth of the raw brewery COUNT, as a companion to the
    proportional-symbol version.

    WHY BOTH. Symbols avoid the area bias that makes a choropleth of counts
    misleading -- fill is read as density-by-area, so a large rural county with
    3 breweries draws more ink than a small dense one with 30. That bias is
    real and is why the symbol map exists. But symbols are genuinely harder to
    read precisely, especially for mid-range values and in the dense Northeast,
    and readers asked for a fill version. Both ship; the caption on this one
    names the bias rather than leaving it to be discovered.

    Zero gets its own near-white class rather than being folded into the lowest
    bin: 55% of counties have no listed brewery, and "none" is a different
    claim from "one or two".
    """
    gdf, count_col, source_note = attach_union_count(gdf.copy())
    gdf["_value"] = gdf[count_col]

    gdf_conus = gdf.to_crs(epsg=5070)
    territory_fips = {"02", "15", "72", "78", "60", "66", "69"}
    conus = gdf_conus[~gdf_conus["STATEFP"].isin(territory_fips)]
    alaska = gdf[gdf["STATEFP"] == "02"].to_crs(epsg=3338)
    hawaii = gdf[gdf["STATEFP"] == "15"].to_crs(epsg=3563)

    vmax = float(pd.concat([conus["_value"], alaska["_value"], hawaii["_value"]]).max())
    bins = [b for b in COUNT_BINS if b < vmax] + [vmax + 1e-9]
    labels = []
    for i in range(len(bins) - 1):
        lo, hi = bins[i], bins[i + 1]
        if lo == 0:
            labels.append("0")
        elif i == len(bins) - 2:
            labels.append(f"{lo:.0f}-{vmax:.0f}")
        elif hi - lo == 1:
            labels.append(f"{lo:.0f}")
        else:
            labels.append(f"{lo:.0f}-{hi - 1:.0f}")
    norm = BoundaryNorm(bins, CMAP.N)

    def draw(ax, sub):
        nonzero = sub[sub["_value"] > 0]
        zero = sub[sub["_value"] == 0]
        if len(zero):
            zero.plot(ax=ax, color=COUNT_ZERO_COLOR, edgecolor="#cccccc", linewidth=0.12)
        if len(nonzero):
            nonzero.plot(ax=ax, column="_value", cmap=CMAP, norm=norm,
                          edgecolor="#999999", linewidth=0.12)
        if len(sub):
            sub.dissolve(by="STATEFP").boundary.plot(
                ax=ax, color=STATE_EDGE_COLOR, linewidth=STATE_EDGE_WIDTH, zorder=4)
        ax.set_axis_off()

    fig = plt.figure(figsize=(16, 10), facecolor=PAGE_COLOR)
    ax = fig.add_axes((0.02, 0.08, 0.96, 0.86))
    ax.set_facecolor(PAGE_COLOR)
    draw(ax, conus)
    # Titles say what the map SHOWS, not how it was built. "(raw count, no
    # model)" meant something to whoever wrote it and nothing to a general
    # reader, who has no reason to know the project has a modelled alternative.
    # The methodology belongs in the caption, where it already is.
    ax.set_title("Breweries per US County",
                  fontsize=17, fontweight="bold", pad=12)

    ax_ak = fig.add_axes((0.02, 0.05, 0.20, 0.22))
    draw(ax_ak, alaska)
    ax_ak.set_title("AK", fontsize=9)
    ax_hi = fig.add_axes((0.20, 0.05, 0.10, 0.14))
    draw(ax_hi, hawaii)
    ax_hi.set_title("HI", fontsize=9)

    legend_elems = [Patch(facecolor=COUNT_ZERO_COLOR, edgecolor="#cccccc", label="0")]
    legend_elems += [
        Patch(facecolor=CMAP(norm((bins[i] + bins[i + 1]) / 2)), edgecolor="#999999",
              label=labels[i]) for i in range(1, len(labels))]
    legend = ax.legend(handles=legend_elems, loc="lower right", bbox_to_anchor=(0.99, 0.01),
                        title="Breweries in county", fontsize=9, title_fontsize=10, frameon=False)

    fig.canvas.draw()
    reserved = [legend.get_window_extent(renderer=fig.canvas.get_renderer())]

    # Label the highest-COUNT counties, which is a different list from the
    # rate map's -- metros dominate here and largely vanish there. That
    # difference is the finding, so the labels should make it legible.
    pool = conus[conus["_value"] > 0].nlargest(60, "_value")
    candidates = [
        LabelCandidate(text=f"{row['NAMELSAD']}, {row['state_abbr']}",
                        x=row.geometry.centroid.x, y=row.geometry.centroid.y,
                        priority=float(row["_value"]))
        for _, row in pool.iterrows()]
    n = place_labels(fig, ax, candidates, max_labels=MAX_COUNT_LABELS, reserved_boxes=reserved)
    print(f"  Labels placed: {n} of {len(candidates)} candidates")

    total = int(pd.concat([conus["_value"], alaska["_value"], hawaii["_value"]]).sum())
    fig.text(0.5, 0.01,
              f"Brewery listings per county, {total:,} nationally. Larger counties cover more "
              "area, so they draw the eye more than small dense ones with the same count. "
              "Listings are incomplete by an estimated 7-54% depending on the state.\n"
              "Sources: Open Brewery DB, OpenStreetMap, US Census.",
              ha="center", fontsize=7.2, color="#555555", wrap=True)

    fig.savefig(out_path, dpi=180, bbox_inches="tight", facecolor=PAGE_COLOR)
    plt.close(fig)
    print(f"Wrote {out_path}")


# Bins for the model-free per-capita map, per 100,000 adults 21+.
RATE_BINS = [0, 1, 2, 4, 7, 11, 16]
# A county's rate is faded by the Poisson relative standard error of its own
# count, 1/sqrt(n): one brewery carries 100% RSE, four 50%, twenty-five 20%.
# This is the model-free analogue of the posterior-width fade on the modelled
# map, and it exists to stop a single brewery in a tiny county reading as a
# national hotspot -- the failure the population floor used to guard against,
# handled continuously instead of by exclusion.
RATE_RELIABLE_RSE = 0.20   # n = 25, drawn at full strength
RATE_UNRELIABLE_RSE = 1.00  # n = 1, faded to MIN_ALPHA


def build_rate_choropleth(gdf: gpd.GeoDataFrame, out_path: str) -> None:
    """Per-capita brewery rate from the UNION counts, with NO model.

    WHY THIS EXISTS. A raw-count map is substantially a population map --
    log(count) against log(adults 21+) gives r = 0.73, so population explains
    about half the variance, and the top-10-by-count and top-10-by-rate lists
    share exactly one county (Boulder CO). Count answers "how many breweries
    are here"; rate answers "where is brewing concentrated". They are different
    questions and both deserve an answer.

    The project's other rate map runs the OBDB-only count through the BYM2
    model. This one divides the union count straight by population: the better
    numerator (13.9% from registry truth vs 34.6%, memo 18.12) and no modelling
    assumptions at all. It is the direct per-capita companion to the count map
    -- same records, same absence of a model, one divided by population.
    """
    gdf, count_col, source_note = attach_union_count(gdf.copy())
    gdf["_count"] = gdf[count_col]
    with np.errstate(divide="ignore", invalid="ignore"):
        gdf["_value"] = np.where(gdf["adults_21plus"] > 0,
                                  gdf["_count"] / gdf["adults_21plus"] * 100_000, np.nan)
        # Poisson RSE of the observed count; undefined at zero, where the rate
        # is exactly 0 and needs no fading.
        rse = np.where(gdf["_count"] > 0, 1.0 / np.sqrt(gdf["_count"].clip(lower=1)), 0.0)
    span = np.log(RATE_UNRELIABLE_RSE) - np.log(RATE_RELIABLE_RSE)
    r = 1.0 - (np.log(np.clip(rse, RATE_RELIABLE_RSE, RATE_UNRELIABLE_RSE))
               - np.log(RATE_RELIABLE_RSE)) / span
    gdf["_strength"] = np.where(gdf["_count"] > 0, MIN_ALPHA + (1 - MIN_ALPHA) * r, 1.0)

    gdf_conus = gdf.to_crs(epsg=5070)
    territory_fips = {"02", "15", "72", "78", "60", "66", "69"}
    conus = gdf_conus[~gdf_conus["STATEFP"].isin(territory_fips)]
    alaska = gdf[gdf["STATEFP"] == "02"].to_crs(epsg=3338)
    hawaii = gdf[gdf["STATEFP"] == "15"].to_crs(epsg=3563)

    drawn = pd.concat([conus["_value"], alaska["_value"], hawaii["_value"]]).dropna()
    vmax = float(drawn.max())
    bins = [b for b in RATE_BINS if b < vmax] + [vmax + 1e-9]
    # Open-ended top class. The maximum is set by a county with a handful of
    # breweries over a few hundred adults (317/100k), so printing it as
    # "16-317" hands the legend to an outlier and makes every real value look
    # like nothing. The fade already de-emphasises those counties; the label
    # should not re-advertise them.
    labels = [f"{bins[i]:.0f}-{bins[i+1]:.0f}" if i < len(bins) - 2
              else f"{bins[i]:.0f}+" for i in range(len(bins) - 1)]
    norm = BoundaryNorm(bins, CMAP.N)

    def draw(ax, sub):
        vals = sub["_value"].to_numpy(dtype=float)
        colors = CMAP(norm(np.nan_to_num(vals, nan=0.0)))
        colors = fade_to_page(colors, sub["_strength"].to_numpy(dtype=float))
        colors[np.isnan(vals)] = list(to_rgb(NO_DATA_COLOR)) + [1.0]
        sub.plot(ax=ax, color=colors, edgecolor="#999999", linewidth=0.12)
        if len(sub):
            sub.dissolve(by="STATEFP").boundary.plot(
                ax=ax, color=STATE_EDGE_COLOR, linewidth=STATE_EDGE_WIDTH, zorder=4)
        ax.set_axis_off()

    fig = plt.figure(figsize=(16, 10), facecolor=PAGE_COLOR)
    ax = fig.add_axes((0.02, 0.08, 0.96, 0.86))
    ax.set_facecolor(PAGE_COLOR)
    draw(ax, conus)
    ax.set_title("Breweries per 100,000 Adults 21+, by US County",
                  fontsize=17, fontweight="bold", pad=12)

    ax_ak = fig.add_axes((0.02, 0.05, 0.20, 0.22))
    draw(ax_ak, alaska)
    ax_ak.set_title("AK", fontsize=9)
    ax_hi = fig.add_axes((0.20, 0.05, 0.10, 0.14))
    draw(ax_hi, hawaii)
    ax_hi.set_title("HI", fontsize=9)

    legend_elems = [Patch(facecolor=CMAP(norm((bins[i] + bins[i+1]) / 2)), edgecolor="#999999",
                           label=labels[i]) for i in range(len(labels))]
    legend = ax.legend(handles=legend_elems, loc="lower right", bbox_to_anchor=(0.99, 0.01),
                        title="Breweries per 100k\nadults 21+", fontsize=9,
                        title_fontsize=10, frameon=False)
    fig.canvas.draw()
    reserved = [legend.get_window_extent(renderer=fig.canvas.get_renderer())]

    # Label top-rate counties, but only where the rate rests on enough
    # breweries to mean something -- otherwise the labels advertise noise.
    pool = conus[(conus["_count"] >= 5) & conus["_value"].notna()].nlargest(60, "_value")
    candidates = [
        LabelCandidate(text=f"{row['NAMELSAD']}, {row['state_abbr']}",
                        x=row.geometry.centroid.x, y=row.geometry.centroid.y,
                        priority=float(row["_value"]))
        for _, row in pool.iterrows()]
    n = place_labels(fig, ax, candidates, max_labels=MAX_COUNT_LABELS, reserved_boxes=reserved)
    print(f"  Labels placed: {n} of {len(candidates)} candidates")

    total = int(pd.concat([conus["_count"], alaska["_count"], hawaii["_count"]]).sum())
    fig.text(0.5, 0.01,
              f"Brewery listings ({total:,} nationally) per 100,000 adults aged 21 and over. "
              "Counties are shown fainter where the rate rests on only a handful of breweries "
              "and is correspondingly uncertain. Listings are incomplete by an estimated 7-54% "
              "depending on the state.\n"
              "Sources: Open Brewery DB, OpenStreetMap, US Census ACS 2020-2024.",
              ha="center", fontsize=7.2, color="#555555", wrap=True)

    fig.savefig(out_path, dpi=180, bbox_inches="tight", facecolor=PAGE_COLOR)
    plt.close(fig)
    print(f"Wrote {out_path}")


def main() -> None:
    gdf = load_county_geodata()
    build_map(gdf, "data/processed/us_brewery_density_choropleth_uncertainty.png",
              floor=None, encode_uncertainty=True)
    build_map(gdf, "data/processed/us_brewery_density_choropleth.png", floor=None)
    build_map(gdf, "data/processed/us_brewery_density_choropleth_floored.png",
              floor=POPULATION_FLOOR)
    build_count_map(gdf, "data/processed/us_brewery_count_map.png")
    build_count_choropleth(gdf, "data/processed/us_brewery_count_choropleth.png")
    build_rate_choropleth(gdf, "data/processed/us_brewery_rate_choropleth.png")


if __name__ == "__main__":
    main()
