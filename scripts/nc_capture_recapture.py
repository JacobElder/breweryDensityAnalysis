"""OBDB x OSM capture-recapture estimate of true NC brewery count, cross-checked
against NC ABC permits and Brewers Association's state total.
"""

from __future__ import annotations

import pandas as pd

from breweries.capture_recapture import lincoln_petersen, match_records
from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import obdb, osm

pd.set_option("display.width", 160)
pd.set_option("display.max_colwidth", 40)


def main() -> None:
    df_obdb = obdb.load_state("North Carolina")
    df_obdb = obdb.apply_inclusion_rule(df_obdb, "NC")
    df_obdb = fill_missing_coords(df_obdb, "id", "latitude", "longitude", "address_1", "city",
                                   "state_province", "postal_code", "obdb_nc")
    df_obdb = df_obdb[df_obdb["latitude"].notna() & df_obdb["longitude"].notna()].reset_index(drop=True)

    df_osm = osm.load_state("NC")

    matched = match_records(
        df_obdb, df_osm,
        name_a="name", name_b="name",
        lat_a="latitude", lon_a="longitude", lat_b="lat", lon_b="lon",
    )

    n1 = len(df_obdb)
    n2 = len(df_osm)
    m = (matched["matched_b_index"] >= 0).sum()

    result = lincoln_petersen(n1, n2, int(m))

    print("=" * 70)
    print("Capture-recapture: OBDB x OSM, North Carolina")
    print("=" * 70)
    print(f"OBDB (list 1, n1):                {n1}")
    print(f"OSM  (list 2, n2):                 {n2}")
    print(f"Matched in both (m):               {m}")
    print(f"OBDB-only:                         {n1 - m}")
    print(f"OSM-only:                          {n2 - m}")
    print()
    print(f"Chapman estimate of true N:        {result['n_hat']:.1f}")
    print(f"95% CI:                            [{result['ci_low']:.1f}, {result['ci_high']:.1f}]")
    print(f"Implied OBDB capture rate:         {result['capture_rate_1']:.1%}")
    print(f"Implied OSM capture rate:          {result['capture_rate_2']:.1%}")
    print()
    print("External cross-check (not part of the estimator):")
    print(f"  NC ABC active AE-Brewery permits: 422")
    print(f"  Brewers Association NC (2025):    418")
    print()
    print("!! The Chapman N_hat is ~1.9x the ABC/BA administrative totals. This is not")
    print("   read as 'true count is ~800' — it is the signature of violated independence")
    print("   (heterogeneous catchability): OBDB and OSM are both volunteer/crowdsourced")
    print("   platforms, so a brewery's odds of being listed on one correlate with its odds")
    print("   of being listed on the other (online visibility, being well-established, etc.),")
    print("   which inflates two-sample capture-recapture estimates. Loosening the match")
    print("   radius from 300m to 2000m only recovers ~9 more matches (92->101), confirming")
    print("   this is a real coverage gap, not a matching-threshold artifact.")
    print("   RECOMMENDATION: use the direct OBDB/ABC ratio (~62% statewide, computed in")
    print("   build_nc_county_dataset.py checkpoint 3) as the coverage correction, not this")
    print("   estimator — ABC is an administrative registry with different (uncorrelated)")
    print("   capture mechanics, which is the assumption this method actually needs.")

    unmatched_low_quality = matched[(matched["matched_b_index"] < 0)]
    print(f"\n{len(unmatched_low_quality)} OBDB records with no OSM match within 300m + name-similarity >= 65")

    out = matched[["id", "name", "city", "matched_b_index", "match_name_score", "match_distance_m"]]
    out.to_csv("data/processed/nc_obdb_osm_match.csv", index=False)
    print("Wrote data/processed/nc_obdb_osm_match.csv for manual audit")


if __name__ == "__main__":
    main()
