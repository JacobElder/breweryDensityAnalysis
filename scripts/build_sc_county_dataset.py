"""Assemble the South Carolina county-level analysis dataset and run validation checkpoints 1-3.

South Carolina calibration state, added specifically to check the covariate residual
model's flag on Charleston County, SC (Model B's top national residual result — "more
breweries than covariates predict") against a ground-truth count. Note: a separate
leave-one-state-out validation already showed Charleston's residual ranking drops when
SC's own data is excluded from training; this dataset is a different, complementary
check — whether OBDB's raw Charleston/SC brewery count is itself accurate, not a re-run
of that modeling validation.

Unlike NC/MI/CO/OR/WA/TX/GA/WI/PA, this state has **no fourth (state-licensor) source**.
South Carolina Department of Revenue licenses breweries under a "Brewery Permit (PWY)"
(see https://dor.sc.gov/tax/abl/licenses/brewery) and a separate "Liquor Manufacturer
License (PML)" for distilled spirits, but SCDOR does not publish any bulk/open-data
export of its Alcohol Beverage Licensing (ABL) roster:

  - No Socrata/CKAN open-data portal entry was found for SC ABL data (data.sc.gov does
    not resolve as a live open-data domain; catalog.data.gov has nothing for SC alcohol
    licensing — the one "Alcohol Beverage Services" hit that surfaced is Montgomery
    County, MD, unrelated).
  - SCDOR's own "Valid ABL licenses and permits" tool
    (https://mydorway.dor.sc.gov/?link=alcohollicense) is not a static report or API:
    a plain HTTP GET returns only "Your browser appears to have cookies disabled.
    Cookies are required to use this site." It is a session/cookie-gated interactive
    lookup embedded in the MyDORWAY tax portal, i.e. exactly the kind of interactive
    search-form tool this project's hard rule says not to scrape.
  - Unlike NC's ABC Commission (which, despite its own record-level search being
    Cloudflare-blocked, exposes a working POST report-generator endpoint returning
    county-level AE-Brewery permit counts as .xlsx — see breweries.sources.nc_abc),
    no equivalent SC DOR report-generator endpoint could be found. A previously
    indexed "ABL License Issued In Last 30 Days" PDF
    (dor.sc.gov/tax-index/abl/Documents/ABL%20License%20Issued%20In%20Last%2030%20Days.pdf)
    now 404s, and even if live would only cover new issuances in a rolling 30-day
    window, not the full active roster.

Per the project's hard rule ("if a data source is unavailable... stop and report it;
do not substitute a plausible-looking alternative"), no src/breweries/sources/sc_*.py
module was written and no fourth source is included below. This script still builds
the OBDB + OSM + CBP + ACS county dataset and runs the three checkpoints against
what IS available, with the missing fourth source called out explicitly rather than
silently omitted.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, obdb, osm

pd.set_option("display.width", 160)

# Checked 2026-08-30, single state-total lookup at
# https://www.brewersassociation.org/statistics-and-data/state-craft-beer-stats/
# ("122 Craft Breweries (Ranks 26th)" for South Carolina, 2025 vintage).
BA_SC_TOTAL_2025 = 122


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("South Carolina")
    df = obdb.apply_inclusion_rule(df, "SC")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_sc")
    geo = assign_geographies(df, "latitude", "longitude", "SC", "obdb_sc")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("SC")
    geo = assign_geographies(df, "lat", "lon", "SC", "osm_sc")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("SC")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("SC", "county")
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

    # ACS denominators cover all 46 SC counties; absence in a count column means a true
    # zero for OBDB/OSM. CBP suppression is handled upstream (kept as NaN there); CBP's
    # absence-from-response here also means zero establishments in the NAICS-312120-only
    # query, per Census CBP convention of omitting zero-count county/industry combos.
    for col in ["obdb_count", "osm_count", "cbp_estab"]:
        df[col] = df[col].fillna(0).astype(int)

    df["obdb_rate_per_100k_21plus"] = df["obdb_count"] / df["adults_21plus"] * 100_000

    out_path = Path("data/processed/sc_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} counties)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (Charleston County should show a meaningful brewery")
    print("count -- it's the specific county Model B flagged as a top national residual)")
    print("=" * 70)
    top = df[df["adults_21plus"] >= 20_000].sort_values("obdb_rate_per_100k_21plus", ascending=False)
    print(top[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].head(10).to_string(index=False))

    chas = df[df["county_name"] == "Charleston"]
    if len(chas):
        row = chas.iloc[0]
        print(f"\nCharleston County directly: obdb_count={int(row['obdb_count'])}  "
              f"osm_count={int(row['osm_count'])}  cbp_estab={int(row['cbp_estab'])}  "
              f"adults_21plus={int(row['adults_21plus'])}  "
              f"obdb_rate_per_100k_21plus={row['obdb_rate_per_100k_21plus']:.1f}")
    else:
        print("\nWARNING: Charleston County not found in the merged dataset.")

    print("\n" + "=" * 70)
    print("CHECKPOINT 2: State rollup vs Brewers Association")
    print("=" * 70)
    print(f"OBDB statewide total (this pipeline's inclusion rule): {df['obdb_count'].sum()}")
    print(f"Brewers Association SC total (2025, checked 2026-08-30): {BA_SC_TOTAL_2025}")
    pct_diff = (df["obdb_count"].sum() - BA_SC_TOTAL_2025) / BA_SC_TOTAL_2025 * 100
    print(f"OBDB vs BA: {pct_diff:+.1f}%")

    print("\n" + "=" * 70)
    print("CHECKPOINT 3: Cross-source agreement (statewide totals) + capture rate vs BA")
    print("No state-licensor (4th) source: SC DOR does not publish a bulk/open-data ABL")
    print("roster -- see module docstring for what was checked and ruled out.")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum()),
        ("Brewers Association (2025)", BA_SC_TOTAL_2025),
    ]:
        capture = val / BA_SC_TOTAL_2025 * 100
        print(f"{label:62s} {val:5d}   ({capture:5.1f}% of BA total)")

    print("\nTop 10 counties by OBDB count, three sources side by side:")
    cmp = df.sort_values("obdb_count", ascending=False).head(10)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab"]].to_string(index=False))


if __name__ == "__main__":
    main()
