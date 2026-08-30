"""Assemble the Pennsylvania county-level analysis dataset and run validation
checkpoints 1-3.

PA is a calibration state and a control state (PLCB directly retails wine and
spirits), the first control state in this project's calibration set. Breweries
are still privately licensed manufacturers, not PLCB retail operations, so a
brewery license count is available the same way it is for the license-based
states -- see src/breweries/sources/pa_liquor.py for the full inclusion-rule
writeup, including the "Alcohol Beverage" license-consolidation quirk and the
companion-license dedup this source needed that CO/OR/MI did not.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, obdb, osm, pa_liquor

pd.set_option("display.width", 160)

# Checked https://www.brewersassociation.org/statistics-and-data/state-craft-beer-stats/
# on 2026-08-30 (single state-total lookup, no directory scrape): PA has 538 craft
# breweries, ranked #2 nationally.
BA_PA_TOTAL_2025 = 538


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("Pennsylvania")
    df = obdb.apply_inclusion_rule(df, "PA")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_pa")
    geo = assign_geographies(df, "latitude", "longitude", "PA", "obdb_pa")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("PA")
    geo = assign_geographies(df, "lat", "lon", "PA", "osm_pa")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("PA")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("PA", "county")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "total_population", "adults_21plus"]]


def build_liquor_county_counts() -> pd.DataFrame:
    # pa_liquor already carries PLCB's own County column -- no geocoding/spatial
    # join needed (unlike CO/OR's lat-lon-based sources), see module docstring.
    df = pa_liquor.county_counts()
    return df.rename(columns={"pa_liquor_count": "liquor_count"})


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

    out_path = Path("data/processed/pa_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} counties)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (Philadelphia + Allegheny/Pittsburgh should rank prominently)")
    print("=" * 70)
    top = df[df["adults_21plus"] >= 50_000].sort_values("obdb_rate_per_100k_21plus", ascending=False)
    print(top[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].head(10).to_string(index=False))
    print("\nRaw counts for Philadelphia and Allegheny specifically:")
    print(df[df["county_name"].isin(["Philadelphia", "Allegheny"])][
        ["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count", "adults_21plus"]
    ].to_string(index=False))

    print("\n" + "=" * 70)
    print("CHECKPOINT 2/3: State rollup + cross-source agreement vs BA")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum()),
        ("PA PLCB (active Brewery + Brewery Pub + brewery-named Alcohol Beverage)", df["liquor_count"].sum()),
        ("Brewers Association (2025)", BA_PA_TOTAL_2025),
    ]:
        capture = val / BA_PA_TOTAL_2025 * 100
        print(f"{label:72s} {val:5d}   ({capture:5.1f}% of BA total)")

    print("\nTop 10 counties, all four sources side by side:")
    cmp = df.sort_values("liquor_count", ascending=False).head(10)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count"]].to_string(index=False))


if __name__ == "__main__":
    main()
