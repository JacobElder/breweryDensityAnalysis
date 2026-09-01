"""Generate geometry + data JSON for the interactive brewery density map artifact.

Simplifies TIGER county and CBSA polygons (Douglas-Peucker, ~1.5km tolerance —
imperceptible at national-map scale, ~200x fewer points than the raw TIGER
geometry), projects CONUS to Albers Equal Area and Alaska/Hawaii to their own
inset projections (matching build_choropleth.py), and emits everything as a
single JSON file consumed by the hand-authored HTML/JS artifact (no mapping
library — CSP blocks external scripts, and a hand-rolled SVG renderer is
lighter than embedding one).
"""

from __future__ import annotations

import json
import re

import pandas as pd
from shapely.geometry import MultiPolygon, Polygon
from shapely.affinity import affine_transform

from breweries.capture_rate_model import CALIBRATED_STATE_CAPTURE_RATES
from breweries.sources import tiger

TERRITORY_FIPS = {"02", "15", "72", "78", "60", "66", "69"}
SIMPLIFY_TOLERANCE_M = 1500

# Census CBSA titles always end "<city list>, <ST[-ST...]> Metro/Micro Area",
# with one state abbreviation per constituent county's state (deduped),
# hyphen-joined in the order Census lists them — e.g. "Charlotte-Concord-
# Gastonia, NC-SC Metro Area". Parsing the title avoids needing a separate
# CBSA-to-county crosswalk just to know which states a CBSA touches.
_CBSA_STATE_RE = re.compile(r",\s*([A-Z]{2}(?:-[A-Z]{2})*)\s+(?:Metro|Micro)\s+Area\s*$")


def cbsa_states(cbsa_name: str) -> list[str]:
    m = _CBSA_STATE_RE.search(cbsa_name)
    return m.group(1).split("-") if m else []


def state_capture_rate(state_abbr: str | None) -> float | None:
    """Calibrated capture rate for a state, clipped to 1.0 like correction_factor()
    does (a capture rate is a fraction of a true population and can't exceed 1.0
    by definition — see capture_rate_model.py's docstring for why several raw
    calibration values exceed 1.0). Returns None if the state has no direct
    calibration (i.e. it would fall back to the pooled/extrapolated rate).
    """
    if state_abbr is None:
        return None
    rate = CALIBRATED_STATE_CAPTURE_RATES.get(state_abbr)
    return round(min(rate, 1.0), 3) if rate is not None else None


def fit_transform(bounds, target_w, target_h, pad=0.02):
    """Return an affine transform (a,b,d,e,xoff,yoff) mapping bounds into a
    target_w x target_h box (SVG y-down), preserving aspect ratio, centered.
    """
    minx, miny, maxx, maxy = bounds
    w, h = maxx - minx, maxy - miny
    usable_w, usable_h = target_w * (1 - 2 * pad), target_h * (1 - 2 * pad)
    scale = min(usable_w / w, usable_h / h)
    draw_w, draw_h = w * scale, h * scale
    xoff = target_w * pad + (usable_w - draw_w) / 2 - minx * scale
    # y flip: svg_y = target_h - (y - miny) * scale - top_pad_adjustment
    yoff = target_h * (1 - pad) - (usable_h - draw_h) / 2 + miny * scale
    return (scale, 0, 0, -scale, xoff, yoff)


def geom_to_path(geom, transform) -> str:
    if geom is None or geom.is_empty:
        return ""
    tgeom = affine_transform(geom, transform)
    polys = [tgeom] if isinstance(tgeom, Polygon) else list(tgeom.geoms)
    parts = []
    for poly in polys:
        for ring in [poly.exterior, *poly.interiors]:
            coords = list(ring.coords)
            if len(coords) < 3:
                continue
            d = f"M{coords[0][0]:.1f},{coords[0][1]:.1f}"
            d += "".join(f"L{x:.1f},{y:.1f}" for x, y in coords[1:])
            d += "Z"
            parts.append(d)
    return "".join(parts)


def build_county_layer():
    gdf = tiger.load_counties()
    conus = gdf[~gdf["STATEFP"].isin(TERRITORY_FIPS)].to_crs(epsg=5070)
    conus = conus.copy()
    conus["geometry"] = conus.simplify(SIMPLIFY_TOLERANCE_M)
    ak = gdf[gdf["STATEFP"] == "02"].to_crs(epsg=3338)
    ak = ak.copy()
    ak["geometry"] = ak.simplify(SIMPLIFY_TOLERANCE_M)
    hi = gdf[gdf["STATEFP"] == "15"].to_crs(epsg=3563)
    hi = hi.copy()
    hi["geometry"] = hi.simplify(SIMPLIFY_TOLERANCE_M)

    VIEW_W, VIEW_H = 1600, 900
    conus_tf = fit_transform(conus.total_bounds, VIEW_W * 0.98, VIEW_H * 0.90, pad=0.01)
    ak_tf_raw = fit_transform(ak.total_bounds, 260, 190, pad=0.05)
    hi_tf_raw = fit_transform(hi.total_bounds, 150, 110, pad=0.05)
    AK_ORIGIN = (20, VIEW_H - 210)
    HI_ORIGIN = (290, VIEW_H - 175)

    def offset_tf(tf, origin):
        a, b, d, e, xo, yo = tf
        return (a, b, d, e, xo + origin[0], yo + origin[1])

    ak_tf = offset_tf(ak_tf_raw, AK_ORIGIN)
    hi_tf = offset_tf(hi_tf_raw, HI_ORIGIN)

    paths = {}
    for _, row in conus.iterrows():
        paths[row["GEOID"]] = geom_to_path(row["geometry"], conus_tf)
    for _, row in ak.iterrows():
        paths[row["GEOID"]] = geom_to_path(row["geometry"], ak_tf)
    for _, row in hi.iterrows():
        paths[row["GEOID"]] = geom_to_path(row["geometry"], hi_tf)

    return paths, {
        "viewW": VIEW_W, "viewH": VIEW_H,
        "akBox": [AK_ORIGIN[0], AK_ORIGIN[1], 260, 190],
        "hiBox": [HI_ORIGIN[0], HI_ORIGIN[1], 150, 110],
    }


def build_cbsa_layer(view_dims):
    gdf = tiger.load_cbsas()
    gdf = gdf.copy()
    proj = gdf.to_crs(epsg=5070)
    proj["geometry"] = proj.simplify(SIMPLIFY_TOLERANCE_M)
    VIEW_W, VIEW_H = view_dims["viewW"], view_dims["viewH"]
    tf = fit_transform(proj.total_bounds, VIEW_W * 0.98, VIEW_H * 0.90, pad=0.01)
    paths = {}
    for _, row in proj.iterrows():
        paths[row["CBSAFP"]] = geom_to_path(row["geometry"], tf)
    return paths


def load_county_data() -> dict:
    # The project's adopted headline model (covariates + state FE + BYM2
    # spatial random effect) is the map's default rate; the plain
    # empirical-Bayes shrunken rate ships alongside it as a toggleable
    # alternative, same as raw/floored already are -- see methods memo
    # Section 15 for why the combined model was adopted.
    df = pd.read_parquet("data/processed/us_county_shrunken_rankings.parquet")
    df["county_geoid"] = df["county_geoid"].astype(str).str.zfill(5)
    df = df.sort_values("eb_posterior_rate_per_100k", ascending=False).reset_index(drop=True)
    df["rank_all"] = df.index + 1
    floored = df[df["adults_21plus"] >= 50_000].sort_values(
        "eb_posterior_rate_per_100k", ascending=False).reset_index(drop=True)
    floored["rank_floored"] = floored.index + 1
    df = df.merge(floored[["county_geoid", "rank_floored"]], on="county_geoid", how="left")

    combined = pd.read_parquet("data/processed/us_county_combined_model_rankings.parquet")
    combined["county_geoid"] = combined["county_geoid"].astype(str).str.zfill(5)
    combined = combined.sort_values("combined_posterior_rate_per_100k", ascending=False).reset_index(drop=True)
    combined["rank_combined"] = combined.index + 1
    df = df.merge(
        combined[["county_geoid", "combined_posterior_rate_per_100k", "combined_ci_low_per_100k",
                  "combined_ci_high_per_100k", "spatial_smoothing_applied", "rank_combined"]],
        on="county_geoid", how="left",
    )

    out = {}
    for _, r in df.iterrows():
        state_abbr = r["state_abbr"] if pd.notna(r["state_abbr"]) else None
        capture_rate = state_capture_rate(state_abbr)
        out[r["county_geoid"]] = {
            "name": r["county_name"],
            "state": r["state_abbr"],
            "count": int(r["obdb_count"]),
            "pop21": int(r["adults_21plus"]),
            "raw": round(float(r["obdb_rate_per_100k_21plus"]), 2),
            "shrunk": round(float(r["eb_posterior_rate_per_100k"]), 2),
            "ciLow": round(float(r["eb_ci_low_per_100k"]), 2),
            "ciHigh": round(float(r["eb_ci_high_per_100k"]), 2),
            "combined": round(float(r["combined_posterior_rate_per_100k"]), 2),
            "combinedCiLow": round(float(r["combined_ci_low_per_100k"]), 2) if pd.notna(r["combined_ci_low_per_100k"]) else None,
            "combinedCiHigh": round(float(r["combined_ci_high_per_100k"]), 2) if pd.notna(r["combined_ci_high_per_100k"]) else None,
            "spatialSmoothed": bool(r["spatial_smoothing_applied"]) if pd.notna(r["spatial_smoothing_applied"]) else False,
            "rank": int(r["rank_all"]),
            "rankFloored": int(r["rank_floored"]) if pd.notna(r["rank_floored"]) else None,
            "rankCombined": int(r["rank_combined"]) if pd.notna(r["rank_combined"]) else None,
            # Calibration confidence: whether this county's state has a real,
            # independently-measured OBDB capture rate (CALIBRATED_STATE_CAPTURE_RATES)
            # vs. the much less certain pooled/extrapolated rate every other state uses.
            "calibrated": capture_rate is not None,
            "captureRate": capture_rate,
        }
    return out


def load_cbsa_data() -> dict:
    df = pd.read_parquet("data/processed/us_cbsa_analysis.parquet")
    df["cbsa_geoid"] = df["cbsa_geoid"].astype(str).str.zfill(5)
    df = df.sort_values("eb_posterior_rate_per_100k", ascending=False).reset_index(drop=True)
    df["rank_all"] = df.index + 1
    out = {}
    for _, r in df.iterrows():
        states = cbsa_states(r["cbsa_name"])
        rates = [state_capture_rate(s) for s in states]
        calibrated_rates = [rt for rt in rates if rt is not None]
        # A CBSA can span multiple states (e.g. "Charlotte-Concord-Gastonia,
        # NC-SC"). "full" = every constituent state independently calibrated
        # (captureRate = mean of those states' rates — an approximation when
        # >1 state, since the map shows one number per CBSA); "partial" = some
        # but not all constituent states calibrated; "none" = none are. Only
        # "full" is treated as calibrated for the map's visual indicator, since
        # a CBSA is only as trustworthy as its least-calibrated constituent state.
        if states and len(calibrated_rates) == len(states):
            calib_status = "full"
            capture_rate = round(sum(calibrated_rates) / len(calibrated_rates), 3)
        elif calibrated_rates:
            calib_status = "partial"
            capture_rate = None
        else:
            calib_status = "none"
            capture_rate = None
        out[r["cbsa_geoid"]] = {
            "name": r["cbsa_name"],
            "count": int(r["obdb_count"]),
            "pop21": int(r["adults_21plus"]),
            "raw": round(float(r["obdb_rate_per_100k_21plus"]), 2),
            "shrunk": round(float(r["eb_posterior_rate_per_100k"]), 2),
            "rank": int(r["rank_all"]),
            "states": states,
            "calibrated": calib_status == "full",
            "calibStatus": calib_status,
            "captureRate": capture_rate,
        }
    return out


def main() -> None:
    print("Building county geometry...")
    county_paths, view_dims = build_county_layer()
    print(f"  {len(county_paths)} county paths, "
          f"{sum(len(p) for p in county_paths.values()) / 1e6:.2f}MB of path data")

    print("Building CBSA geometry...")
    cbsa_paths = build_cbsa_layer(view_dims)
    print(f"  {len(cbsa_paths)} cbsa paths, "
          f"{sum(len(p) for p in cbsa_paths.values()) / 1e6:.2f}MB of path data")

    print("Loading rate data...")
    county_data = load_county_data()
    cbsa_data = load_cbsa_data()

    payload = {
        "view": view_dims,
        "countyPaths": county_paths,
        "countyData": county_data,
        "cbsaPaths": cbsa_paths,
        "cbsaData": cbsa_data,
        "calibratedStates": sorted(CALIBRATED_STATE_CAPTURE_RATES),
    }
    out_path = "data/processed/interactive_map_data.json"
    with open(out_path, "w") as f:
        json.dump(payload, f, separators=(",", ":"))

    import os
    size_mb = os.path.getsize(out_path) / 1e6
    print(f"\nWrote {out_path} ({size_mb:.2f}MB)")


if __name__ == "__main__":
    main()
