"""Assemble the Missouri county-level analysis dataset and run validation
checkpoints 1-3.

Calibration state. Liquor-registry source is breweries.sources.mo_liquor --
the Missouri Division of Alcohol and Tobacco Control's own statewide
"Primary Alcohol Licenses" export via data.mo.gov (Socrata), linked from
ATC's own reports page. See that module's docstring for the full
inclusion-rule writeup, including two structural coverage gaps documented
there rather than worked around: the source's "Microbrewery" license class
does not capture Missouri's handful of large/regional breweries (Anheuser-
Busch, Boulevard) under any primary_type, and does not distinguish
brewpubs from ordinary restaurant retail licenses.

## Why this script merges on county FIPS GEOID, not bare county name
(same reasoning and pattern as build_va_county_dataset.py)

Missouri has exactly one independent city, St. Louis city, which is its own
Census county-equivalent, separate from -- but confusingly same-named as --
the adjacent St. Louis County. TIGER's bare `NAME` column (what
`assign_geographies()` returns as `county_name` for OBDB/OSM) is literally
"St. Louis" for both, so merging on that bare name would silently fold St.
Louis city's brewery count into St. Louis County's (or vice versa). Every
source here is instead resolved to a 5-digit county FIPS GEOID before
merging, exactly as build_va_county_dataset.py does for Richmond/Roanoke/
Franklin/Fairfax:
  - OBDB / OSM go through `assign_geographies()`'s TIGER spatial join, which
    carries `county_geoid` (unambiguous) alongside the ambiguous bare
    `county_name` -- this script uses `county_geoid` and ignores
    `county_name` from that path entirely.
  - MO ATC's own `county` field (used directly, no geocoding -- see
    mo_liquor.py) already disambiguates "St. Louis city" from "St. Louis
    County"; normalized to match TIGER's NAMELSAD format and resolved to a
    GEOID via a crosswalk.
  - CBP and ACS return `state` + `county` FIPS-code columns directly
    alongside their human-readable `NAME` (which, like TIGER's NAMELSAD,
    also disambiguates city vs. county -- but the FIPS columns avoid any
    text-matching at all).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.census_client import STATE_FIPS
from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, mo_liquor, obdb, osm, tiger

pd.set_option("display.width", 160)

# Checked https://www.brewersassociation.org/statistics-and-data/state-craft-beer-stats/
# on 2026-08-31 (single state-total lookup, no directory scrape): Missouri has 130
# craft breweries.
BA_MO_TOTAL_2025 = 130  # checked 2026-08-31, single state-total lookup


def _mo_geoid_crosswalk() -> pd.DataFrame:
    """GEOID -> canonical display name (TIGER NAMELSAD) for all 115 Missouri
    county-equivalents. NAMELSAD, unlike TIGER's bare NAME, already carries
    the "city"/"County" suffix that disambiguates St. Louis city from St.
    Louis County -- see module docstring."""
    counties = tiger.load_counties(STATE_FIPS["MO"])
    cw = counties[["GEOID", "NAMELSAD"]].rename(
        columns={"GEOID": "county_geoid", "NAMELSAD": "county_name"}
    )
    return cw.reset_index(drop=True)


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("Missouri")
    df = obdb.apply_inclusion_rule(df, "obdb_mo")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_mo")
    geo = assign_geographies(df, "latitude", "longitude", "MO", "obdb_mo")
    counts = geo.groupby("county_geoid", dropna=True).size().rename("obdb_count").reset_index()
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("MO")
    geo = assign_geographies(df, "lat", "lon", "MO", "osm_mo")
    counts = geo.groupby("county_geoid", dropna=True).size().rename("osm_count").reset_index()
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("MO")
    df["county_geoid"] = df["state"].astype(str).str.zfill(2) + df["county"].astype(str).str.zfill(3)
    return df[["county_geoid", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("MO", "county")
    df["county_geoid"] = df["state"].astype(str).str.zfill(2) + df["county"].astype(str).str.zfill(3)
    return df[["county_geoid", "total_population", "adults_21plus"]]


def build_liquor_county_counts(crosswalk: pd.DataFrame) -> pd.DataFrame:
    df = mo_liquor.county_counts()
    lookup = crosswalk.copy()
    lookup["_key"] = lookup["county_name"].str.strip().str.lower()
    df["_key"] = df["county_name"].str.strip().str.lower()

    merged = df.merge(lookup[["_key", "county_geoid"]], on="_key", how="left")
    n_unmatched = merged["county_geoid"].isna().sum()
    if n_unmatched:
        bad = sorted(merged.loc[merged["county_geoid"].isna(), "county_name"].unique())
        raise RuntimeError(f"MO liquor county_name values with no TIGER match: {bad}")

    counts = merged.groupby("county_geoid")["mo_liquor_count"].sum().reset_index()
    return counts.rename(columns={"mo_liquor_count": "liquor_count"})


def main() -> None:
    crosswalk = _mo_geoid_crosswalk()

    obdb_counts = build_obdb_county_counts()
    osm_counts = build_osm_county_counts()
    cbp_counts = build_cbp_county_counts()
    acs_denom = build_acs_county_denominators()
    liquor_counts = build_liquor_county_counts(crosswalk)

    df = crosswalk.merge(acs_denom, on="county_geoid", how="left")
    df = df.merge(obdb_counts, on="county_geoid", how="left")
    df = df.merge(osm_counts, on="county_geoid", how="left")
    df = df.merge(cbp_counts, on="county_geoid", how="left")
    df = df.merge(liquor_counts, on="county_geoid", how="left")

    for col in ["obdb_count", "osm_count", "cbp_estab", "liquor_count"]:
        df[col] = df[col].fillna(0).astype(int)

    df["obdb_rate_per_100k_21plus"] = df["obdb_count"] / df["adults_21plus"] * 100_000

    out_path = Path("data/processed/mo_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} county-equivalents)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (Jackson Co./KC and St. Louis City/County")
    print("should dominate absolute counts, but may rank lower per-capita)")
    print("=" * 70)
    top = df[df["adults_21plus"] >= 50_000].sort_values("obdb_rate_per_100k_21plus", ascending=False)
    print(top[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].head(15).to_string(index=False))
    print("\nRaw counts for St. Louis city vs. St. Louis County specifically:")
    print(df[df["county_name"].isin(["St. Louis city", "St. Louis County"])][
        ["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count", "adults_21plus"]
    ].to_string(index=False))

    print("\n" + "=" * 70)
    print("CHECKPOINT 2/3: State rollup + cross-source agreement vs BA")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum()),
        ("MO ATC (Microbrewery license class only -- see mo_liquor.py)", df["liquor_count"].sum()),
        ("Brewers Association (2025)", BA_MO_TOTAL_2025),
    ]:
        capture = val / BA_MO_TOTAL_2025 * 100
        print(f"{label:62s} {val:5d}   ({capture:5.1f}% of BA total)")

    print("\nTop 15 county-equivalents, all four sources side by side:")
    cmp = df.sort_values("liquor_count", ascending=False).head(15)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count"]].to_string(index=False))

    print("\nRaw capture rate (aggregated): "
          f"{df['liquor_count'].sum()} / {df['obdb_count'].sum()} obdb = "
          f"{df['liquor_count'].sum() / df['obdb_count'].sum() * 100:.1f}%")


if __name__ == "__main__":
    main()
