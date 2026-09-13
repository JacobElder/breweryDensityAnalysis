"""Apply post-geocode record hygiene to the national OBDB geocode.

Reads  data/processed/obdb_us_geocoded.parquet        (raw geocode output)
Writes data/processed/obdb_us_geocoded_clean.parquet  (what the analysis uses)
       data/processed/obdb_hygiene_report.csv         (every record acted on)

Runs offline against the cached TIGER place/county polygons -- no re-geocode
and no network needed, so this can be re-run cheaply whenever the reviewed
exclusion tables in `breweries.obdb_hygiene` change.

See `breweries.obdb_hygiene` for why each pass has the policy it does.
"""

from __future__ import annotations

import warnings

import geopandas as gpd
import pandas as pd

from breweries import obdb_hygiene as hyg
from breweries.sources import tiger
from breweries.state_fips import STATE_FIPS_ALL

IN_PATH = "data/processed/obdb_us_geocoded.parquet"
OUT_PATH = "data/processed/obdb_us_geocoded_clean.parquet"
REPORT_PATH = "data/processed/obdb_hygiene_report.csv"


def build_place_county_lookup() -> tuple[pd.DataFrame, gpd.GeoDataFrame]:
    """For every incorporated place in every state: the set of county GEOIDs
    its polygon intersects, plus the place polygons themselves (projected to
    CONUS Albers for the distance test).

    A place can straddle a county line (and many do), so the county answer is
    a set, not a single county -- a record is only a misassignment candidate
    if its county is in none of them.
    """
    counties = tiger.load_counties()[["GEOID", "STATEFP", "geometry"]]
    rows = []
    geom_frames = []
    for state_abbr in sorted(STATE_FIPS_ALL):
        fips = STATE_FIPS_ALL[state_abbr]
        places = tiger.load_place(state_abbr)[["NAME", "geometry"]].copy()
        places["state_abbr"] = state_abbr
        places["place_name_norm"] = hyg.normalize_name(places["NAME"])
        geom_frames.append(places[["state_abbr", "place_name_norm", "geometry"]])

        state_counties = counties[counties["STATEFP"] == fips][["GEOID", "geometry"]]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            joined = gpd.sjoin(places, state_counties, how="left", predicate="intersects")
        grouped = joined.groupby("place_name_norm")["GEOID"].apply(lambda s: frozenset(s.dropna()))
        for name_norm, geoids in grouped.items():
            rows.append({"state_abbr": state_abbr, "place_name_norm": name_norm,
                         "county_geoids": geoids})

    lookup = pd.DataFrame(rows)
    # A normalized name can repeat within a state (e.g. a city and a CDP of the
    # same name); union their county sets so the check stays conservative.
    lookup = (lookup.groupby(["state_abbr", "place_name_norm"])["county_geoids"]
              .apply(lambda s: frozenset().union(*s)).reset_index())
    place_geoms = gpd.GeoDataFrame(
        pd.concat(geom_frames, ignore_index=True), crs="EPSG:4326").to_crs(epsg=5070)
    print(f"Place -> county lookup: {len(lookup)} (state, place) pairs")
    return lookup, place_geoms


def main() -> None:
    df = pd.read_parquet(IN_PATH)
    df["county_geoid"] = df["county_geoid"].astype("string").str.zfill(5)
    n_start = len(df)
    print(f"Loaded {n_start} geocoded OBDB records from {IN_PATH}\n")

    report_rows = []

    # --- Pass 0: ungeocoded records, reported rather than silently dropped ---
    # build_national_county_dataset.py previously did a bare
    # `.dropna(subset=["county_geoid"])`, so these vanished without ever
    # appearing in a count or a log line.
    ungeocoded = df[df["county_geoid"].isna()]
    print(f"Pass 0 -- ungeocoded records (no county assignment): {len(ungeocoded)} "
          f"({len(ungeocoded) / n_start:.1%} of all records)")
    if len(ungeocoded):
        by_state = ungeocoded["state_abbr"].value_counts()
        print(f"  Worst-affected states: {by_state.head(8).to_dict()}")
        print("  These are excluded from every county-level count. Because they are "
              "concentrated by state, they add to the state-level OBDB coverage gap "
              "the capture-rate model already corrects for.")
    for _, r in ungeocoded.iterrows():
        report_rows.append({"action": "ungeocoded_excluded", "name": r["name"],
                            "city": r["city"], "state_abbr": r["state_abbr"],
                            "county_name": None, "reason": "no lat/lon or no county polygon match"})

    # --- Pass 1: non-brewery contamination -------------------------------
    print("\nPass 1 -- non-brewery records that passed the brewery_type filter:")
    flagged = hyg.flag_non_brewery_candidates(df)
    n_candidates = int(flagged["non_brewery_candidate"].sum())
    print(f"  {n_candidates} record(s) name a competing beverage category with no brewing token")
    before = len(df)
    keyed = list(zip(hyg.normalize_name(df["name"]), df["state_abbr"].fillna("")))
    for (k, r) in zip(keyed, df.itertuples()):
        if k in hyg.REVIEWED_NON_BREWERY_BY_NAME:
            report_rows.append({"action": "dropped_non_brewery", "name": r.name,
                                "city": r.city, "state_abbr": r.state_abbr,
                                "county_name": r.county_name,
                                "reason": hyg.REVIEWED_NON_BREWERY_BY_NAME[k]})
    df = hyg.drop_reviewed_non_breweries(df)
    print(f"  {before} -> {len(df)} records")

    # --- Pass 2: duplicate physical locations ----------------------------
    print("\nPass 2 -- duplicate entries for one physical location:")
    dup_flagged = hyg.flag_duplicate_locations(df)
    for _, r in dup_flagged[dup_flagged["is_duplicate_secondary"]].iterrows():
        report_rows.append({"action": "dropped_duplicate", "name": r["name"], "city": r["city"],
                            "state_abbr": r["state_abbr"], "county_name": r["county_name"],
                            "reason": f"shares exact coordinates with a kept record ({r['dup_group']})"})
    before = len(df)
    df = hyg.collapse_duplicate_locations(df)
    print(f"  {before} -> {len(df)} records")

    # --- Pass 3: county misassignment ------------------------------------
    print("\nPass 3 -- county misassignment (street-name collision in geocoding):")
    lookup, place_geoms = build_place_county_lookup()
    bad = hyg.find_county_misassignments(df, lookup, place_geoms)
    # Correct only cases that are BOTH far from the stated city (so it is a
    # street-name collision, not a mailing address in an unincorporated area)
    # AND unambiguous (the stated city lies in exactly one county). Everything
    # else is reported and left alone -- see obdb_hygiene's module docstring.
    n_fixed = 0
    for idx, r in bad.iterrows():
        if r["reassign_to"] is not None:
            df.loc[idx, "county_geoid"] = r["reassign_to"]
            df.loc[idx, "county_name"] = pd.NA  # stale; re-derived downstream from geoid
            n_fixed += 1
            action, reason = "county_reassigned", (
                f"stated city {r['city']}, {r['state_abbr']} is {r['km_from_stated_city']:.1f} km away "
                f"and lies only in {r['reassign_to']}; was assigned {r['county_geoid']} ({r['county_name']})")
        elif r["km_from_stated_city"] <= hyg.MIN_MISASSIGN_KM:
            action, reason = "county_mismatch_geocode_kept", (
                f"{r['km_from_stated_city']:.1f} km from stated city {r['city']} -- mailing address in an "
                f"unincorporated area; geocode to {r['county_geoid']} treated as correct")
        else:
            action, reason = "county_mismatch_unresolved", (
                f"{r['km_from_stated_city']:.1f} km from stated city {r['city']}, which straddles "
                f"{r['expected_county_geoids']}; left at {r['county_geoid']}")
        report_rows.append({"action": action, "name": r["name"], "city": r["city"],
                            "state_abbr": r["state_abbr"], "county_name": r["county_name"],
                            "reason": reason})
    print(f"  Reassigned {n_fixed} record(s) to the county containing their stated city")

    # --- Write -----------------------------------------------------------
    df.to_parquet(OUT_PATH, index=False)
    report = pd.DataFrame(report_rows)
    report.to_csv(REPORT_PATH, index=False)

    print("\n" + "=" * 70)
    print(f"{n_start} raw records -> {len(df)} analysis records "
          f"({n_start - len(df)} removed, {len(ungeocoded)} of them ungeocoded)")
    print(f"Wrote {OUT_PATH}")
    print(f"Wrote {REPORT_PATH} ({len(report)} rows)")
    if len(report):
        print("\nActions taken:")
        print(report["action"].value_counts().to_string())


if __name__ == "__main__":
    main()
