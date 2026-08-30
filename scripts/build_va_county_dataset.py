"""Assemble the Virginia county-level analysis dataset and run validation
checkpoints 1-3.

Virginia is a calibration state and a second control state (Virginia ABC
directly retails spirits), structurally like Pennsylvania -- but exactly as
with PA's PLCB, brewing is still privately licensed, not run by the state, so
a brewery license count is available the same way it is for the license-based
states. See src/breweries/sources/va_abc.py for the full inclusion-rule
writeup (Brewery vs. Limited Brewery / farm-brewery license types) and its
export-glitch dedup.

## Why this script does NOT merge on bare county name, unlike every other
## calibration state's build script

Virginia has independent cities that are their own county-equivalent,
*alongside* a same-named county: Richmond city vs. Richmond County, Roanoke
city vs. Roanoke County, Franklin city vs. Franklin County, Fairfax city vs.
Fairfax County (Alexandria, Bristol, Charlottesville, etc. are independent
cities too, but have no same-named county to collide with). Census TIGER's
bare `NAME` column -- which every other calibration state's script merges on,
via `assign_geographies()`'s `county_name` output -- is literally identical
for Richmond city and Richmond County ("Richmond"), and likewise for the
other three pairs. Merging on that bare name here would silently fold
Richmond city's brewery count into Richmond County's (or vice versa,
depending on join order) -- exactly the kind of error this project's
face-validity checkpoint exists to catch, since Richmond city is one of the
two regions this state's checkpoint specifically watches.

Instead, every source here is resolved to a 5-digit county FIPS GEOID before
merging:
  - OBDB / OSM / VA ABC liquor points already go through
    `assign_geographies()`'s TIGER spatial join, which carries `county_geoid`
    (unambiguous) alongside the ambiguous bare `county_name` -- this script
    uses `county_geoid` and ignores that `county_name` column entirely.
  - VA ABC's own COUNTY field (used directly, no geocoding -- see
    va_abc.py) already disambiguates city vs. county via a "city"/"County"
    suffix; it's matched case-insensitively against TIGER's NAMELSAD to
    resolve to the same GEOID.
  - CBP and ACS return `state` + `county` FIPS-code columns directly
    alongside their human-readable `NAME` (which, unlike TIGER's bare NAME,
    *does* include the "city"/"County" suffix and would work too -- but the
    FIPS columns are simpler and avoid any text-matching at all).

A single crosswalk (`_va_geoid_crosswalk`, built from TIGER's NAMELSAD, e.g.
"Richmond city" / "Richmond County") supplies the human-readable
`county_name` used for display in every printed checkpoint below.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.census_client import STATE_FIPS
from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, obdb, osm, tiger, va_abc

pd.set_option("display.width", 160)

# Checked https://www.brewersassociation.org/statistics-and-data/state-craft-beer-stats/
# on 2026-08-30 (single state-total lookup, no directory scrape): VA has 344 craft
# breweries (Ranks 11th nationally; 5.2 breweries per 100k 21+ adults, Ranks 16th).
BA_VA_TOTAL_2025 = 344  # checked 2026-08-30, single state-total lookup


def _va_geoid_crosswalk() -> pd.DataFrame:
    """GEOID -> canonical display name (TIGER NAMELSAD) for all 133 VA
    county-equivalents. NAMELSAD, unlike TIGER's bare NAME, already carries
    the "city"/"County" suffix that disambiguates e.g. Richmond city from
    Richmond County -- see module docstring."""
    counties = tiger.load_counties(STATE_FIPS["VA"])
    cw = counties[["GEOID", "NAMELSAD"]].rename(
        columns={"GEOID": "county_geoid", "NAMELSAD": "county_name"}
    )
    return cw.reset_index(drop=True)


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("Virginia")
    df = obdb.apply_inclusion_rule(df, "VA")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_va")
    geo = assign_geographies(df, "latitude", "longitude", "VA", "obdb_va")
    counts = geo.groupby("county_geoid", dropna=True).size().rename("obdb_count").reset_index()
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("VA")
    geo = assign_geographies(df, "lat", "lon", "VA", "osm_va")
    counts = geo.groupby("county_geoid", dropna=True).size().rename("osm_count").reset_index()
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("VA")
    df["county_geoid"] = df["state"].astype(str).str.zfill(2) + df["county"].astype(str).str.zfill(3)
    return df[["county_geoid", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("VA", "county")
    df["county_geoid"] = df["state"].astype(str).str.zfill(2) + df["county"].astype(str).str.zfill(3)
    return df[["county_geoid", "total_population", "adults_21plus"]]


def build_liquor_county_counts(crosswalk: pd.DataFrame) -> pd.DataFrame:
    df = va_abc.load()
    lookup = crosswalk.copy()
    lookup["_key"] = lookup["county_name"].str.strip().str.lower()
    df["_key"] = df["county_name"].str.strip().str.lower()

    merged = df.merge(lookup[["_key", "county_geoid"]], on="_key", how="left")
    n_unmatched = merged["county_geoid"].isna().sum()
    if n_unmatched:
        bad = sorted(merged.loc[merged["county_geoid"].isna(), "county_name"].unique())
        raise RuntimeError(f"VA ABC county_name values with no TIGER match: {bad}")

    counts = merged.groupby("county_geoid").size().rename("liquor_count").reset_index()
    return counts


def main() -> None:
    crosswalk = _va_geoid_crosswalk()

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

    out_path = Path("data/processed/va_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} county-equivalents)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (Richmond city + Roanoke area should rank prominently)")
    print("=" * 70)
    top = df[df["adults_21plus"] >= 50_000].sort_values("obdb_rate_per_100k_21plus", ascending=False)
    print(top[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].head(10).to_string(index=False))
    print("\nRaw counts for Richmond city and the Roanoke-area county-equivalents specifically:")
    print(df[df["county_name"].isin(["Richmond city", "Richmond County", "Roanoke city", "Roanoke County"])][
        ["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count", "adults_21plus", "obdb_rate_per_100k_21plus"]
    ].to_string(index=False))

    print("\n" + "=" * 70)
    print("CHECKPOINT 2/3: State rollup + cross-source agreement vs BA")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum()),
        ("VA ABC (active Industry Brewery License: Brewery + Limited Brewery)", df["liquor_count"].sum()),
        ("Brewers Association (2025)", BA_VA_TOTAL_2025),
    ]:
        capture = val / BA_VA_TOTAL_2025 * 100
        print(f"{label:72s} {val:5d}   ({capture:5.1f}% of BA total)")

    print("\nTop 10 county-equivalents, all four sources side by side:")
    cmp = df.sort_values("liquor_count", ascending=False).head(10)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count"]].to_string(index=False))


if __name__ == "__main__":
    main()
