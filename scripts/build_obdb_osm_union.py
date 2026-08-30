"""Union OBDB with OSM to systematically catch single-source gaps (e.g. a brewery
present on OSM but missing from OBDB, or vice versa) without per-city manual review.

This is deliberately NOT capture-recapture. Capture-recapture tries to estimate
the population that NEITHER source observed, by extrapolating from the overlap —
that failed badly for OBDB/OSM (src/breweries/capture_recapture.py,
docs/methods_memo.md Sec. 5.3) because the two sources' capture probabilities are
correlated (both crowdsourced, both more likely to catch well-established/visible
breweries), which biases any extrapolation. A union has no such bias: it only
combines what was *actually observed* by at least one source, using the same
name+distance matching already built and validated for the NC diagnostic. It
cannot catch a brewery missing from both sources (e.g. Balefire Brewing,
Santa Cruz — confirmed absent from both during manual investigation); only a
ground-truth registry catches that class of gap, which is why this project is
expanding state calibration coverage rather than treating this as a substitute.
"""

from __future__ import annotations

import pandas as pd

from breweries.capture_recapture import match_records, normalize_name, _haversine_m
from breweries.geocode import fill_missing_coords
from breweries.sources import obdb, osm
from breweries.state_fips import STATE_FIPS_ALL

import numpy as np


def dedupe_osm_internal(df: pd.DataFrame, max_distance_m: float = 300) -> pd.DataFrame:
    """OSM sometimes has the same physical brewery as two nodes (e.g. a building
    outline and a POI point, or a re-survey that didn't remove the old node) —
    found in a manual spot-check (~1.7% of CA's OSM brewery records). Collapse
    same-name, near-identical-location duplicates to one row before matching
    against OBDB, so they don't each separately register as a "new" addition.
    """
    df = df.reset_index(drop=True).copy()
    df["_norm"] = df["name"].map(normalize_name)
    keep = np.ones(len(df), dtype=bool)
    for i in range(len(df)):
        if not keep[i]:
            continue
        d = _haversine_m(df.loc[i, "lat"], df.loc[i, "lon"], df["lat"].values, df["lon"].values)
        for j in range(i + 1, len(df)):
            if keep[j] and d[j] <= max_distance_m and df.loc[j, "_norm"] == df.loc[i, "_norm"]:
                keep[j] = False
    return df[keep].drop(columns="_norm").reset_index(drop=True)


def union_one_state(state_abbr: str, obdb_state: pd.DataFrame, obdb_state_all_types: pd.DataFrame) -> pd.DataFrame:
    """Return OSM records with no match in the INCLUDED OBDB set, split into:
    - genuinely absent from OBDB entirely (real candidate gaps), vs.
    - present in OBDB under an excluded brewery_type (planning/closed/contract/...
      — the inclusion filter is working as intended, this isn't missing data,
      it's OBDB itself saying "not currently an operating independent brewery").
    Distinguishing these matters: only the first group is evidence OBDB's count
    understates *included* breweries; the second is a definitional match, not a gap.
    """
    osm_state = osm.load_state(state_abbr)
    if len(osm_state) == 0:
        return osm_state.iloc[0:0]
    osm_state = dedupe_osm_internal(osm_state)

    if len(obdb_state) > 0:
        matched = match_records(
            osm_state, obdb_state,
            name_a="name", name_b="name",
            lat_a="lat", lon_a="lon", lat_b="latitude", lon_b="longitude",
        )
        osm_only = matched[matched["matched_b_index"] < 0].copy()
    else:
        osm_only = osm_state.copy()
        osm_only["matched_b_index"] = -1

    if len(osm_only) == 0 or len(obdb_state_all_types) == 0:
        osm_only["also_in_obdb_excluded_type"] = False
        osm_only["obdb_excluded_type"] = None
        return osm_only[["name", "lat", "lon", "also_in_obdb_excluded_type", "obdb_excluded_type"]]

    against_all = match_records(
        osm_only, obdb_state_all_types,
        name_a="name", name_b="name",
        lat_a="lat", lon_a="lon", lat_b="latitude", lon_b="longitude",
    )
    also_in = against_all["matched_b_index"] >= 0
    osm_only = osm_only.copy()
    osm_only["also_in_obdb_excluded_type"] = also_in.values
    osm_only["obdb_excluded_type"] = [
        obdb_state_all_types.iloc[int(idx)]["brewery_type"] if idx >= 0 else None
        for idx in against_all["matched_b_index"].values
    ]
    return osm_only[["name", "lat", "lon", "also_in_obdb_excluded_type", "obdb_excluded_type"]]


def main() -> None:
    obdb_us_all_types = obdb.load_us()  # every brewery_type, for the "excluded not missing" check
    obdb_us = obdb.apply_inclusion_rule(obdb_us_all_types, "US")
    # Without this, any OBDB record missing lat/lon (~22% of them, before this
    # fallback) has NaN coordinates, so distance to every OSM record is NaN and
    # the comparison in match_records always evaluates false — the record can
    # never match, regardless of true proximity, and gets miscounted as a novel
    # "OSM-only" addition even when OBDB already has it. Found via a manual
    # spot-check (Maine Beer Co, Cellarmaker, Denizens... all already in OBDB,
    # all missing lat/lon in the raw CSV) before trusting the union counts.
    obdb_us = fill_missing_coords(obdb_us, "id", "latitude", "longitude", "address_1",
                                   "city", "state_province", "postal_code", "obdb_us_union")
    obdb_us_all_types = fill_missing_coords(obdb_us_all_types, "id", "latitude", "longitude",
                                             "address_1", "city", "state_province", "postal_code",
                                             "obdb_us_union_alltypes")
    from breweries.state_fips import STATE_NAME_TO_ABBR
    obdb_us["state_abbr"] = obdb_us["state_province"].map(STATE_NAME_TO_ABBR)
    obdb_us_all_types["state_abbr"] = obdb_us_all_types["state_province"].map(STATE_NAME_TO_ABBR)

    all_osm_only = []
    print(f"{'State':6s} {'OBDB':>6s} {'OSM-only (raw)':>16s}")
    for state_abbr in sorted(STATE_FIPS_ALL):
        obdb_state = obdb_us[obdb_us["state_abbr"] == state_abbr]
        obdb_state_all = obdb_us_all_types[obdb_us_all_types["state_abbr"] == state_abbr]
        osm_only = union_one_state(state_abbr, obdb_state, obdb_state_all)
        osm_only["state_abbr"] = state_abbr
        all_osm_only.append(osm_only)
        print(f"{state_abbr:6s} {len(obdb_state):6d} {len(osm_only):16d}")

    osm_only_national = pd.concat(all_osm_only, ignore_index=True)
    genuinely_new = osm_only_national[~osm_only_national["also_in_obdb_excluded_type"]]
    excluded_type_matches = osm_only_national[osm_only_national["also_in_obdb_excluded_type"]]
    total_obdb = len(obdb_us)

    print(f"\nOBDB total (included types): {total_obdb}")
    print(f"OSM-only, raw (before splitting): {len(osm_only_national)}")
    print(f"  of which already in OBDB under an EXCLUDED type (planning/closed/contract/...): "
          f"{len(excluded_type_matches)} — not a coverage gap, the filter is working as intended")
    if len(excluded_type_matches):
        print(f"    excluded-type breakdown: {excluded_type_matches['obdb_excluded_type'].value_counts().to_dict()}")
    print(f"  genuinely absent from OBDB entirely: {len(genuinely_new)}")
    print(f"\nUnion total (genuine candidates only): {total_obdb + len(genuinely_new)} "
          f"({len(genuinely_new) / total_obdb:.1%} more than OBDB alone)")
    print("NOTE: this is still an UPPER BOUND, not a validated count — spot-checking during")
    print("development found remaining false positives from imperfect name matching (e.g.")
    print("compound/dual-brand OBDB names like 'X / Y Alehouse') even after two rounds of")
    print("bug fixes. Treat 'genuinely_new' as candidates for review, not confirmed brewery")
    print("counts, without further matching refinement or manual spot-checks.")

    osm_only_national.to_csv("data/processed/obdb_osm_union_additions.csv", index=False)
    print("\nWrote data/processed/obdb_osm_union_additions.csv "
          "(all OSM-only records with the excluded-type flag, for manual review)")


if __name__ == "__main__":
    main()
