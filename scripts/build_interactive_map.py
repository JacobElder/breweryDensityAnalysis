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

import pandas as pd
from shapely.geometry import MultiPolygon, Polygon
from shapely.affinity import affine_transform

from breweries.sources import tiger

TERRITORY_FIPS = {"02", "15", "72", "78", "60", "66", "69"}
SIMPLIFY_TOLERANCE_M = 1500


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
    df = pd.read_parquet("data/processed/us_county_shrunken_rankings.parquet")
    df["county_geoid"] = df["county_geoid"].astype(str).str.zfill(5)
    df = df.sort_values("eb_posterior_rate_per_100k", ascending=False).reset_index(drop=True)
    df["rank_all"] = df.index + 1
    floored = df[df["adults_21plus"] >= 50_000].sort_values(
        "eb_posterior_rate_per_100k", ascending=False).reset_index(drop=True)
    floored["rank_floored"] = floored.index + 1
    df = df.merge(floored[["county_geoid", "rank_floored"]], on="county_geoid", how="left")

    out = {}
    for _, r in df.iterrows():
        out[r["county_geoid"]] = {
            "name": r["county_name"],
            "state": r["state_abbr"],
            "count": int(r["obdb_count"]),
            "pop21": int(r["adults_21plus"]),
            "raw": round(float(r["obdb_rate_per_100k_21plus"]), 2),
            "shrunk": round(float(r["eb_posterior_rate_per_100k"]), 2),
            "ciLow": round(float(r["eb_ci_low_per_100k"]), 2),
            "ciHigh": round(float(r["eb_ci_high_per_100k"]), 2),
            "rank": int(r["rank_all"]),
            "rankFloored": int(r["rank_floored"]) if pd.notna(r["rank_floored"]) else None,
        }
    return out


def load_cbsa_data() -> dict:
    df = pd.read_parquet("data/processed/us_cbsa_analysis.parquet")
    df["cbsa_geoid"] = df["cbsa_geoid"].astype(str).str.zfill(5)
    df = df.sort_values("eb_posterior_rate_per_100k", ascending=False).reset_index(drop=True)
    df["rank_all"] = df.index + 1
    out = {}
    for _, r in df.iterrows():
        out[r["cbsa_geoid"]] = {
            "name": r["cbsa_name"],
            "count": int(r["obdb_count"]),
            "pop21": int(r["adults_21plus"]),
            "raw": round(float(r["obdb_rate_per_100k_21plus"]), 2),
            "shrunk": round(float(r["eb_posterior_rate_per_100k"]), 2),
            "rank": int(r["rank_all"]),
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
    }
    out_path = "data/processed/interactive_map_data.json"
    with open(out_path, "w") as f:
        json.dump(payload, f, separators=(",", ":"))

    import os
    size_mb = os.path.getsize(out_path) / 1e6
    print(f"\nWrote {out_path} ({size_mb:.2f}MB)")


if __name__ == "__main__":
    main()
