"""Assemble the Connecticut county-level analysis dataset and run validation checkpoints 1-3.

Calibration state (following NC, MI, CO, OR, WA, TX, GA, WI, PA, IL, CA, NY, VA,
FL). Liquor-registry source is breweries.sources.ct_dcp -- data.ct.gov's
Socrata "Liquor Permits" open-data set, filtered to active "LMB" (Manufacturer
Permit for Beer) credentials (see that module's docstring for the full
inclusion-rule writeup, including how LMB was identified against three
rejected-but-brewery-adjacent credential prefixes, and why Connecticut's
"county_name" values here are actually the state's 9 Census planning regions,
not its traditional 8 counties).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, ct_dcp, obdb, osm

pd.set_option("display.width", 160)

# Checked https://www.brewersassociation.org/statistics-and-data/state-craft-beer-stats/
# on 2026-08-31 (single state-total lookup, no directory scrape): Connecticut has 121
# craft breweries, ranked #27 nationally.
BA_CT_TOTAL_2025 = 121  # checked 2026-08-31, single state-total lookup


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("Connecticut")
    df = obdb.apply_inclusion_rule(df, "obdb_ct")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_ct")
    geo = assign_geographies(df, "latitude", "longitude", "CT", "obdb_ct")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("CT")
    geo = assign_geographies(df, "lat", "lon", "CT", "osm_ct")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def _ct_region_name(name: pd.Series) -> pd.Series:
    # Census's NAME field for CT's 9 Census-designated planning regions (the
    # county-equivalent geography CT has used since the 2022 vintage, replacing
    # its 8 traditional counties -- see ct_dcp.py module docstring) comes back
    # as "Capitol Planning Region, Connecticut", not "X County, Connecticut"
    # like every other state, so the usual " County," split leaves it
    # untouched. TIGER's own NAME field for the same geography is just
    # "Capitol" (bare), which is what assign_geographies() ultimately produces
    # for the OBDB/OSM/liquor counts below -- so ACS/CBP must be normalized to
    # match here, or every merge on county_name silently returns zero matches.
    return name.str.replace(" Planning Region, Connecticut", "", regex=False)


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("CT")
    df["county_name"] = _ct_region_name(df["NAME"])
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("CT", "county")
    df["county_name"] = _ct_region_name(df["NAME"])
    return df[["county_name", "total_population", "adults_21plus"]]


def build_liquor_county_counts() -> pd.DataFrame:
    # ct_dcp has no county field -- geocode via Census Geocoder fallback and
    # spatial-join to the (planning-region) county-equivalent layer, same
    # mechanism as wi_dor.py / ga_dor.py.
    df = ct_dcp.load()
    df = fill_missing_coords(df, "ct_dcp_id", "lat", "lon", "street_address", "city",
                              "state", "zip", "ct_dcp")
    geo = assign_geographies(df, "lat", "lon", "CT", "ct_dcp")
    counts = geo.groupby("county_name", dropna=True).size().rename("liquor_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def main() -> None:
    obdb_counts = build_obdb_county_counts()
    osm_counts = build_osm_county_counts()
    cbp_counts = build_cbp_county_counts()
    acs_denom = build_acs_county_denominators()
    liquor_counts = build_liquor_county_counts()

    df = acs_denom.merge(obdb_counts, on="county_name", how="left")
    df = df.merge(osm_counts, on="county_name", how="left")
    df = df.merge(cbp_counts, on="county_name", how="left")
    df = df.merge(liquor_counts, on="county_name", how="left")

    for col in ["obdb_count", "osm_count", "cbp_estab", "liquor_count"]:
        df[col] = df[col].fillna(0).astype(int)

    df["obdb_rate_per_100k_21plus"] = df["obdb_count"] / df["adults_21plus"] * 100_000

    out_path = Path("data/processed/ct_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} planning regions)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (Capitol [Hartford] and Greater Bridgeport / South")
    print("Central Connecticut [New Haven] regions should dominate absolute counts, but may")
    print("rank lower per-capita due to population dilution -- same pattern as other")
    print("large-metro geographies already seen in this project)")
    print("=" * 70)
    top = df.sort_values("obdb_rate_per_100k_21plus", ascending=False)
    print(top[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].head(9).to_string(index=False))

    print("\n" + "=" * 70)
    print("CHECKPOINT 2/3: State rollup + cross-source agreement vs BA")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum()),
        ("CT DCP (LMB -- Manufacturer Permit for Beer, active)", df["liquor_count"].sum()),
        ("Brewers Association (2025)", BA_CT_TOTAL_2025),
    ]:
        capture = val / BA_CT_TOTAL_2025 * 100
        print(f"{label:62s} {val:5d}   ({capture:5.1f}% of BA total)")

    print("\nAll 9 planning regions, all four sources side by side:")
    cmp = df.sort_values("liquor_count", ascending=False)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count"]].to_string(index=False))


if __name__ == "__main__":
    main()
