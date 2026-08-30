"""Assemble the Arizona county-level analysis dataset and run validation checkpoints 1-3.

Arizona is NOT a calibration state in the sense NC/MI/CO/OR/WA/TX/GA/WI/PA are:
this project's calibration states each pair OBDB/OSM/CBP against a fourth,
independent leg — the state's own liquor/ABC/DOR licensee registry, pulled from
a genuine bulk open-data source (a Socrata API, a documented CSV/Excel export,
etc., never an interactive per-record search tool). No such source exists for
Arizona.

What was checked (all confirmed dead ends for *bulk, record-level* data):
  - liquor.az.gov ("Business Data Reports", "Reports", "License Search") and
    the legacy azliquor.gov query tools (license_series.cfm,
    license_recordcount.cfm) are Cloudflare-fronted interactive lookup/aggregate
    tools — "enter as much information as you know, and press Search" — with no
    export/CSV/API option and no "return everything" mode. The DLLC's ABC
    Online record search (dllc.azliquor.gov/azdlprod) is the same: a per-record
    search form (Premises / Licensee / License Number / License Type /
    Effective Date), not a bulk list.
  - No Arizona state open-data portal carries a DLLC dataset. `data.az.gov`
    does not resolve to anything; Arizona's actual open-data properties
    (`data.azdhs.gov` — health department dashboards; the AZGeo Data Hub —
    geospatial/GIS layers only) have no licensee registry of any kind, let
    alone liquor licenses.
  - The DLLC's own "Public Records Request" page confirms the only path to a
    full roster is a manual public-records/FOIA-style request — not a
    self-service bulk download, and out of scope for an automated pipeline.

This is the same finding this project already reached for Mississippi (see
docs/methods_memo.md Section 8): "no bulk-downloadable source (only an
interactive per-record search tool, which this project's rules do not permit
scripting around)... A clean 'no,' not a gap in effort." Per that same rule,
no `src/breweries/sources/az_dllc.py` module was written, and no state-registry
leg is merged below — inventing one, or scripting around the interactive
search tool, is exactly what the project's source-integrity rule forbids.

What this script *does* do: assemble the three genuinely independent bulk
sources this project already trusts everywhere (OBDB, OSM, CBP) against ACS
county denominators, so Arizona can still be checked for face validity and
cross-source agreement — in particular Flagstaff/Coconino County, which shows
up in the national county-level ranking table as a top-20 per-capita result
and is the specific reason Arizona was added here. Three independent counting
methodologies agreeing (or not) on Coconino is still informative; it just
isn't the fourth-source state-registry confirmation the other nine states get.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, obdb, osm

pd.set_option("display.width", 160)

BA_AZ_TOTAL_2025 = 124  # checked 2026-08-30, single state-total lookup (brewersassociation.org/statistics-and-data/state-craft-beer-stats/)


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("Arizona")
    df = obdb.apply_inclusion_rule(df, "AZ")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_az")
    geo = assign_geographies(df, "latitude", "longitude", "AZ", "obdb_az")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("AZ")
    geo = assign_geographies(df, "lat", "lon", "AZ", "osm_az")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("AZ")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("AZ", "county")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "total_population", "adults_21plus"]]


def main() -> None:
    obdb_counts = build_obdb_county_counts()
    osm_counts = build_osm_county_counts()
    cbp_counts = build_cbp_county_counts()
    acs_denom = build_acs_county_denominators()

    df = acs_denom.merge(obdb_counts, on="county_name", how="left")
    df = df.merge(osm_counts, on="county_name", how="left")
    df = df.merge(cbp_counts, on="county_name", how="left")

    for col in ["obdb_count", "osm_count"]:
        df[col] = df[col].fillna(0).astype(int)
    # cbp_estab is left as-is: suppressed small cells come back as NaN, never
    # coerced to 0 (see src/breweries/sources/cbp.py docstring).

    df["obdb_rate_per_100k_21plus"] = df["obdb_count"] / df["adults_21plus"] * 100_000

    out_path = Path("data/processed/az_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} counties)\n")
    print("NOTE: no Arizona DLLC bulk licensee source exists (see module docstring) — "
          "this dataset has three independent legs (OBDB, OSM, CBP), not four. "
          "Arizona is not a calibration state in the same sense as NC/MI/CO/OR/WA/TX/GA/WI/PA.\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (Maricopa/Phoenix, Pima/Tucson, Coconino/Flagstaff)")
    print("=" * 70)
    top = df[df["adults_21plus"] >= 20_000].sort_values("obdb_rate_per_100k_21plus", ascending=False)
    print(top[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].head(10).to_string(index=False))

    print("\nExplicit Coconino/Flagstaff check (flagged nationally as a top-20 per-capita county):")
    coconino = df[df["county_name"] == "Coconino"]
    if len(coconino):
        print(coconino[["county_name", "obdb_count", "osm_count", "cbp_estab", "adults_21plus",
                         "obdb_rate_per_100k_21plus"]].to_string(index=False))
    else:
        print("Coconino County not found in the merged dataset.")

    for name in ["Maricopa", "Pima"]:
        row = df[df["county_name"] == name]
        if len(row):
            print(f"\n{name} County:")
            print(row[["county_name", "obdb_count", "osm_count", "cbp_estab", "adults_21plus",
                        "obdb_rate_per_100k_21plus"]].to_string(index=False))

    print("\n" + "=" * 70)
    print("CHECKPOINT 2/3: State rollup + cross-source agreement vs BA (3 legs, no state registry)")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum(skipna=True)),
        ("Brewers Association (2025)", BA_AZ_TOTAL_2025),
    ]:
        capture = val / BA_AZ_TOTAL_2025 * 100
        print(f"{label:62s} {val:5.0f}   ({capture:5.1f}% of BA total)")

    print("\nTop 10 counties, all three sources side by side:")
    cmp = df.sort_values("obdb_count", ascending=False).head(10)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab"]].to_string(index=False))


if __name__ == "__main__":
    main()
