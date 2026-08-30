"""Assemble the NC county-level analysis dataset and run validation checkpoints 1-3.

Vertical slice per the project handoff: get one state working end to end before
scaling nationally. Outputs data/processed/nc_county_analysis.parquet and prints the
face-validity, state-rollup, and cross-source-agreement checkpoints.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, nc_abc, obdb, osm

pd.set_option("display.width", 160)


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("North Carolina")
    df = obdb.apply_inclusion_rule(df, "NC")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_nc")
    geo = assign_geographies(df, "latitude", "longitude", "NC", "obdb_nc")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("NC")
    geo = assign_geographies(df, "lat", "lon", "NC", "osm_nc")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("NC")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("NC", "county")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "total_population", "adults_21plus"]]


def build_abc_county_counts() -> pd.DataFrame:
    df = nc_abc.load_county_counts()
    return df.rename(columns={"county": "county_name", "brewery_permit_count": "abc_permit_count"})


def main() -> None:
    obdb_counts = build_obdb_county_counts()
    osm_counts = build_osm_county_counts()
    cbp_counts = build_cbp_county_counts()
    acs_denom = build_acs_county_denominators()
    abc_counts = build_abc_county_counts()

    df = acs_denom.merge(obdb_counts, on="county_name", how="left")
    df = df.merge(osm_counts, on="county_name", how="left")
    df = df.merge(cbp_counts, on="county_name", how="left")
    df = df.merge(abc_counts, on="county_name", how="left")

    # ACS denominators cover all 100 counties; absence in a count column means a true
    # zero for that source (not suppression) for OBDB/OSM/ABC. CBP suppression is
    # handled upstream (kept as NaN there); CBP's absence-from-response here also
    # means zero establishments in the NAICS-312120-only query, per Census CBP
    # convention of omitting zero-count county/industry combinations entirely.
    for col in ["obdb_count", "osm_count", "cbp_estab", "abc_permit_count"]:
        df[col] = df[col].fillna(0).astype(int)

    df["obdb_rate_per_100k_21plus"] = df["obdb_count"] / df["adults_21plus"] * 100_000

    out_path = Path("data/processed/nc_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} counties)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (Buncombe/Asheville should rank very high)")
    print("=" * 70)
    top = df[df["adults_21plus"] >= 50_000].sort_values("obdb_rate_per_100k_21plus", ascending=False)
    print(top[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].head(10).to_string(index=False))

    # Manually checked once against https://www.brewersassociation.org/statistics-and-data/
    # state-craft-beer-stats/ on 2026-08-30 (per handoff: no bulk download / directory scrape
    # of BA data, single state-total lookup only). BA figure: 418 breweries, 2025 vintage,
    # under BA's independent-ownership definition (<25% owned by a non-craft alcohol company).
    BA_NC_TOTAL_2025 = 418

    print("\n" + "=" * 70)
    print("CHECKPOINT 2: State rollup vs Brewers Association")
    print("=" * 70)
    print(f"OBDB statewide total (this pipeline's inclusion rule): {df['obdb_count'].sum()}")
    print(f"Brewers Association NC total (2025, checked 2026-08-30): {BA_NC_TOTAL_2025}")
    pct_diff = (df["obdb_count"].sum() - BA_NC_TOTAL_2025) / BA_NC_TOTAL_2025 * 100
    print(f"OBDB vs BA: {pct_diff:+.1f}%")

    print("\n" + "=" * 70)
    print("CHECKPOINT 3: Cross-source agreement (statewide totals) + capture rate vs BA/ABC")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum()),
        ("NC ABC (AE-Brewery manufacturing permits, active)", df["abc_permit_count"].sum()),
        ("Brewers Association (2025, independent-ownership definition)", BA_NC_TOTAL_2025),
    ]:
        capture = val / BA_NC_TOTAL_2025 * 100
        print(f"{label:62s} {val:5d}   ({capture:5.1f}% of BA total)")

    print("\nTop 10 counties, all four sources side by side:")
    cmp = df.sort_values("abc_permit_count", ascending=False).head(10)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab", "abc_permit_count"]].to_string(index=False))


if __name__ == "__main__":
    main()
