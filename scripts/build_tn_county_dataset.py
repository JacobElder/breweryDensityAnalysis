"""Assemble the Tennessee county-level analysis dataset and run validation checkpoints 1-3.

Eleventh calibration state (after NC, MI, CO, OR, WA, TX, GA, WI, PA, and whichever
of CA/NY landed alongside this one). Unlike every other calibration state so far,
TENNESSEE HAS NO FOURTH/FIFTH INDEPENDENT LICENSOR SOURCE HERE — this module
combines only OBDB + OSM + CBP + ACS. Per the project's hard rule ("if a data
source is unavailable... stop and report it; do not substitute a plausible-looking
alternative"), no src/breweries/sources/tn_abc.py was written. Reasons, checked
2026-08-30:

1. data.tn.gov is not a live Socrata domain (no state-run open-data catalog
   analogous to CO/OR's data.<state>.gov exists; the domain 403s and the Socrata
   discovery API returns "Domain not found: data.tn.gov"). Tennessee's only
   Socrata-style open-data portals are city-level (Nashville, Memphis,
   Chattanooga), none of which cover a statewide brewery registry.

2. The Tennessee Alcoholic Beverage Commission (TABC) — despite the acronym
   collision with Texas's agency, this is the TN body — structurally does NOT
   regulate ordinary beer. Per TABC's own licensing page: "The TABC does not
   issue beer permits except for the brewing of high gravity beer" (>=8% ABW /
   10.1% ABV). TABC's "Manufacturer" license category covers distilleries only;
   the only beer-adjacent TABC license is the narrow "Brewer of High Gravity
   Beer" license, which the large majority of Tennessee craft breweries (whose
   flagship beers run well under 8% ABW) never need. Ordinary beer manufacturing
   in Tennessee is licensed locally, by ~humdred-plus individual city/county
   beer boards (confirmed via UT's County Technical Assistance Service brief on
   microbreweries/brew pubs), with no statewide aggregation. There is therefore
   no single state license type that is both (a) brewery-specific and (b) covers
   the physical-brewing-location population this project needs, the way CO's
   "Manufacturer (brewery)" or WA's type-326 does.

3. TABC's own license-lookup tool (https://rlpsmobile.abc.tn.gov/LicenseSearch/
   licensesearchindex) is a one-record-at-a-time interactive search form with no
   bulk export, CSV download, or API — exactly the kind of interactive tool this
   project's rules say not to scrape.

4. TABC's public-records page (tn.gov/abc/public-information-and-forms/
   tabc-public-records.html) confirms license data beyond the search tool is
   available only via an open-records request (email/mail/in-person), not a
   self-service bulk download.

5. The TN Department of Revenue's "Approved Alcohol & Beer Brands" list
   (tn.gov/revenue/taxes/alcoholic-beverages-taxes/approved-brands.html) is a
   product/brand registration list (PDF, one year at a time, or via TNTAP), not
   a manufacturer/location registry — it has no address field, mixes in
   out-of-state brands registering to sell into Tennessee, and would need to be
   inverted from "brand name" to "physical TN brewing location," which is not a
   defensible transformation without fabricating a join.

Net: this is a genuine gap in Tennessee's regulatory data landscape (beer is a
local-permitting matter here), not a research shortcut. TN's dataset below runs
on the three independent brewery-count signals used everywhere else in this
project (OBDB, OSM, CBP) plus ACS denominators, without a fourth (licensor)
cross-check. If a future maintainer finds a genuine bulk statewide source (e.g.,
a change in TABC's or DOR's public-data posture), add
src/breweries/sources/tn_abc.py and wire it in the same way co_liquor.py is
wired into build_co_county_dataset.py.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, obdb, osm

pd.set_option("display.width", 160)

BA_TN_TOTAL_2025 = 152  # checked 2026-08-30, single state-total lookup (brewersassociation.org)


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("Tennessee")
    df = obdb.apply_inclusion_rule(df, "TN")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_tn")
    geo = assign_geographies(df, "latitude", "longitude", "TN", "obdb_tn")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("TN")
    geo = assign_geographies(df, "lat", "lon", "TN", "osm_tn")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("TN")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("TN", "county")
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

    for col in ["obdb_count", "osm_count", "cbp_estab"]:
        df[col] = df[col].fillna(0).astype(int)

    df["obdb_rate_per_100k_21plus"] = df["obdb_count"] / df["adults_21plus"] * 100_000

    out_path = Path("data/processed/tn_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} counties)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (Davidson/Nashville, Knox/Knoxville,")
    print("Hamilton/Chattanooga should show up with meaningful brewery counts)")
    print("=" * 70)
    top = df[df["adults_21plus"] >= 20_000].sort_values("obdb_rate_per_100k_21plus", ascending=False)
    print(top[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].head(15).to_string(index=False))

    print("\nBig-3 metro counties specifically:")
    metro = df[df["county_name"].isin(["Davidson", "Knox", "Hamilton", "Shelby"])]
    print(metro[["county_name", "obdb_count", "osm_count", "cbp_estab", "adults_21plus",
                 "obdb_rate_per_100k_21plus"]].to_string(index=False))

    print("\n" + "=" * 70)
    print("CHECKPOINT 2/3: State rollup + cross-source agreement vs BA")
    print("(no fourth/licensor source for TN -- see module docstring)")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum()),
        ("Brewers Association (2025)", BA_TN_TOTAL_2025),
    ]:
        capture = val / BA_TN_TOTAL_2025 * 100
        print(f"{label:62s} {val:5d}   ({capture:5.1f}% of BA total)")

    print("\nTop 15 counties, all three sources side by side:")
    cmp = df.sort_values("obdb_count", ascending=False).head(15)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab"]].to_string(index=False))


if __name__ == "__main__":
    main()
