"""Post-geocode record hygiene for OBDB: non-brewery contamination, duplicate
physical locations, and county misassignment.

WHY THIS EXISTS (and why `brewery_type` filtering isn't enough)
--------------------------------------------------------------
`obdb.apply_inclusion_rule` filters on OBDB's `brewery_type` field, which
excludes `cidery`/`meadery`/`closed`/`planning` outright. That protects
against *correctly typed* non-breweries only. OBDB is crowdsourced, and the
type field is frequently wrong: 113 records that pass the type filter carry a
competing beverage category in their own name, and 31 of those carry no brewing
token at all, e.g.

    Green Bird Cellars and Organic Farms   type=micro      (a winery)
    Citizen Cider                          type=regional   (a cidery)
    Maryland Meadworks                     type=micro      (a meadery)
    Town Branch Distillery                 type=micro      (a distillery)

This was surfaced by a reader who noticed Leelanau County, MI -- Michigan
wine country -- ranking near the top of the national map on five records, two
of which are not breweries. At 18,638 adults 21+ those two records move the
county's raw rate by 10.7 per 100k, so name-level contamination is not a
rounding error in exactly the small counties the map draws most attention to.

THREE PASSES, THREE DIFFERENT POLICIES
--------------------------------------
1. NON-BREWERY NAMES -- auto-*flagged*, manually *reviewed*, then dropped.
   Name matching alone is not decisive: "Sapwood Cellars" (Howard County, MD),
   "Cellar West Artisan Ales" (Boulder, CO), "Raney Cellars" (Lancaster, PA)
   and "Monk's Cellar" (Placer County, CA) are all real, operating breweries,
   while "Green Bird Cellars" is a winery -- the same token, opposite answers.
   So the regex only produces a *review queue* (`flag_non_brewery_candidates`);
   what actually gets dropped is the explicit, per-record, reasoned
   `REVIEWED_NON_BREWERY` table below. Any newly-flagged record that isn't in
   that table is reported loudly and kept, so a refreshed OBDB snapshot can
   never silently drop a real brewery, and can never silently keep a new
   winery either.

2. DUPLICATE PHYSICAL LOCATIONS -- auto-collapsed, but only same-address ones.
   Two distinct patterns hide under "duplicate":
     (a) one location entered twice, e.g. "Peoria Artisan Brewery" at
         "10144 W. Lake Pleasant Parkway Suite 1130" and "10144 W Lake
         Pleasant Pkwy Ste 1130" -- identical coordinates. A true double
         count; collapsed here.
     (b) one brand at several real addresses, e.g. E.J. Phair in Alamo,
         Concord and Pittsburg CA -- three separate physical premises with
         different coordinates. Whether satellite taprooms should count as
         separate "breweries" is a definitional question the project already
         documents (see README, Oregon/OLCC satellite-taproom finding); it is
         NOT a data error, so these are left alone and merely counted in the
         report.
   The rule therefore keys on *coordinates*, never on name alone.

3. COUNTY MISASSIGNMENT -- flagged, and corrected only when far-and-unambiguous.
   The naive check (does the record's stated `city` match the TIGER place it
   landed in?) fires on 412 records and is almost all false positives:
   neighborhood-vs-incorporated-place, e.g. Van Nuys -> Los Angeles, La Jolla
   -> San Diego, Girdwood -> Anchorage. Those geocodes are correct.

   Narrowing to "stated city is an incorporated place in the same state that
   lies outside the assigned county" cuts it to 24 records -- but those still
   mix two populations, separated cleanly by how far the record's coordinates
   sit from its own stated city:

     <= 11 km   mailing address in an unincorporated area near the named city.
                The geocode is RIGHT and the city name is the loose one, e.g.
                Mt. Carmel Brewing (6.0 km outside Cincinnati city limits, in
                Clermont County) or Sister Lakes Brewing (10.8 km from
                Dowagiac, in Van Buren County). Reassigning these would
                introduce errors, not fix them.
     >= 27 km   street-name collision: the geocoder matched the street in the
                wrong place entirely. Every case at this range is the classic
                trap where a town and a *different* county share a name --
                Deer Lodge (town in Powell County, not Deer Lodge County),
                Blue Earth (town in Faribault County, not Blue Earth County),
                Sheridan MT (town in Madison County, 687 km from Sheridan
                County), and the case that prompted this: "Quarter Barrel
                Brewery and Pub, 103 Main St, Hamilton, OH", geocoded to
                (39.125, -84.356) -- a Main St in Cincinnati, 26.8 km away,
                landing it in Hamilton *County* instead of Butler County where
                the *city* of Hamilton sits.

   So reassignment requires BOTH: distance > MIN_MISASSIGN_KM, and the stated
   city resolving to exactly one county. Everything else is reported only.
"""

from __future__ import annotations

import re

import pandas as pd

from breweries.manifest import log_filter

# Tokens that affirmatively indicate brewing. A record naming a competing
# beverage category is only a *candidate* for exclusion if it carries none of
# these -- "Cellar West Artisan Ales" and "Sociable Cider Werks" both name a
# competing category, but only the first also says it makes ale.
BREWING_TOKEN_RE = re.compile(
    r"brew|beer|ale\b|ales\b|alehouse|ale\s+house|alework|hop\b|hops\b|hopped"
    r"|lager|pilsner|stout|porter|saison|brasserie|brauerei|braue|bier(?!\s*distiller)"
    r"|cerveceria|cerveza|brewery|brewing|brewhouse|brewpub|taproom",
    re.IGNORECASE,
)

# Competing beverage categories: wine, cider, mead, spirits, and non-alcoholic
# ferments. Presence of one of these WITHOUT a brewing token queues the record
# for review.
COMPETING_CATEGORY_RE = re.compile(
    r"winer(?:y|ies)\b|vineyard|cellars?\b|wine\s+co|cider|cidery|ciderworks"
    r"|mead\b|meadery|meadworks|distiller|distilling|kombucha|coffee|roaster",
    re.IGNORECASE,
)

# Records confirmed NOT to be breweries by name + a look at what the business
# actually is. Keyed by OBDB `id` so an upstream rename can't silently
# re-admit one. Reason strings are kept because a future OBDB snapshot may
# legitimately reclassify any of these (a winery that adds a brewhouse).
REVIEWED_NON_BREWERY: dict[str, str] = {}

# Name-keyed fallback for the same list. OBDB ids are stable in practice but
# the project has no guarantee of that, so exclusions are matched on
# (normalized name, state) as well -- belt and braces, and it makes the table
# readable. Values are the documented reason.
REVIEWED_NON_BREWERY_BY_NAME: dict[tuple[str, str], str] = {
    # --- Wineries -------------------------------------------------------
    ("vonjakobwinery", "IL"): "winery (Von Jakob Vineyard, Alto Pass IL); typed micro/brewpub in OBDB",
    ("costaventosawineryvineyard", "MD"): "winery, Worcester County MD",
    ("blackfirewinery", "MI"): "winery, Lenawee County MI",
    ("glasscreekwinery", "MI"): "winery, Barry County MI",
    ("greenbirdcellarsandorganicfarms", "MI"): "winery/farm, Leelanau County MI -- the record that surfaced this pass",
    ("heavenlyvineyards", "MI"): "winery, Mecosta County MI",
    ("sandhillcranevineyards", "MI"): "winery, Jackson County MI",
    ("kennedyvineyard", "OH"): "winery, Darke County OH",
    ("lilpawswinery", "OH"): "winery, Mahoning County OH",
    ("landonwinery", "TX"): "winery, Hunt County TX",
    ("whiterockvineyardswinery", "VA"): "winery, Bedford County VA",
    ("oldhousevineyards", "VA"): "winery, Culpeper County VA",
    ("alfalfafarmcellars", "MA"): "Alfalfa Farm Winery, Essex County MA",
    # --- Cideries / meaderies (typed micro/regional, so the type filter missed them) ---
    ("brothersridgecider", "MD"): "cidery, Carroll County MD",
    ("clearskiesmeadery", "MD"): "meadery, Montgomery County MD",
    ("docwaterscidery", "MD"): "cidery, Montgomery County MD",
    ("marylandmeadworks", "MD"): "meadery, Prince George's County MD",
    ("stambrosecellars", "MI"): "meadery, Benzie County MI",
    ("sociableciderwerks", "MN"): "cidery, Hennepin County MN",
    ("shipswheelhardcider", "SC"): "cidery, Charleston County SC",
    ("citizencider", "VT"): "cidery, Chittenden County VT",
    # --- Distilleries ---------------------------------------------------
    ("bierdistillery", "MI"): "distillery, Kent County MI (name is a pun, not a brewery)",
    ("lescheneauxdistillers", "MI"): "distillery, Mackinac County MI",
    # --- Bar, no brewing ------------------------------------------------
    ("sugarfootsaloon", "MI"): "bar/saloon in Cedar MI, Leelanau County; no brewing operation",
}

# Reviewed and deliberately KEPT despite matching the competing-category
# regex. Recorded so the review queue stays empty on a clean run and a human
# reading the report can see these were considered, not missed.
REVIEWED_KEEP_BY_NAME: dict[tuple[str, str], str] = {
    ("monkscellar", "CA"): "Monk's Cellar, Roseville CA -- operating brewpub",
    ("cellarwestartisanales", "CO"): "Cellar West Artisan Ales, Boulder CO -- operating brewery",
    ("sapwoodcellars", "MD"): "Sapwood Cellars, Columbia MD -- operating brewery",
    ("raneycellars", "PA"): "Raney Cellars, Lancaster PA -- operating brewery",
    ("covertartisanalescellars", "SD"): "Covert Artisan Ales & Cellars, Sioux Falls SD -- operating brewery",
    ("therootcellar", "TX"): "The Root Cellar, San Marcos TX -- operating brewpub",
    ("revelurbanwineryrevelotr", "OH"): "Revel OTR, Cincinnati -- brews as well as makes wine",
    ("townbranchdistillery", "KY"): "Alltech Lexington Brewing & Distilling -- brews Kentucky Ale on site",
    ("ebcoffeeandpub", "MI"): "EB Coffee & Pub, Kent County MI -- brewpub",
}


def normalize_name(s: pd.Series) -> pd.Series:
    """Lowercase, strip everything but alphanumerics. Used as the join key for
    the reviewed-exclusion tables and for duplicate detection.

    Coerces to string first: an EMPTY column arrives as float64, and the `.str`
    accessor raises AttributeError on it rather than returning an empty result.
    That turns a legitimate edge case -- no records for a state, an empty
    reference frame -- into a crash several call frames away from the cause.
    """
    return (s.astype("string").fillna("").str.lower()
            .str.replace(r"[^a-z0-9]", "", regex=True))


def flag_non_brewery_candidates(df: pd.DataFrame) -> pd.DataFrame:
    """Add `names_competing_category` / `names_brewing` / `non_brewery_candidate`.

    A candidate is a record naming a competing beverage category with no
    brewing token anywhere in its name. Flagging only -- nothing is dropped
    here.
    """
    out = df.copy()
    name = out["name"].fillna("")
    out["names_competing_category"] = name.str.contains(COMPETING_CATEGORY_RE)
    out["names_brewing"] = name.str.contains(BREWING_TOKEN_RE)
    out["non_brewery_candidate"] = out["names_competing_category"] & ~out["names_brewing"]
    return out


def drop_reviewed_non_breweries(df: pd.DataFrame, *, verbose: bool = True) -> pd.DataFrame:
    """Drop the manually reviewed non-brewery records, and report any *newly*
    flagged candidate that has not yet been reviewed.

    Unreviewed candidates are KEPT (a false positive must never silently
    delete a real brewery) but printed, so refreshing the OBDB snapshot
    surfaces new contamination instead of burying it.
    """
    flagged = flag_non_brewery_candidates(df)
    key = list(zip(normalize_name(flagged["name"]), flagged["state_abbr"].fillna("")))
    flagged["_key"] = key

    is_reviewed_drop = flagged["_key"].map(lambda k: k in REVIEWED_NON_BREWERY_BY_NAME)
    is_reviewed_drop |= flagged["id"].astype(str).isin(REVIEWED_NON_BREWERY)
    is_reviewed_keep = flagged["_key"].map(lambda k: k in REVIEWED_KEEP_BY_NAME)

    unreviewed = flagged[flagged["non_brewery_candidate"] & ~is_reviewed_drop & ~is_reviewed_keep]
    if verbose and len(unreviewed):
        print(f"  NOTE: {len(unreviewed)} newly-flagged non-brewery candidate(s) not yet reviewed "
              "-- KEPT in the counts. Add to REVIEWED_NON_BREWERY_BY_NAME or REVIEWED_KEEP_BY_NAME:")
        for _, r in unreviewed.iterrows():
            print(f"    {r['name']!r} ({r['state_abbr']}, {r['county_name']}, type={r['brewery_type']})")

    kept = flagged[~is_reviewed_drop].drop(columns=["_key"])
    dropped = flagged[is_reviewed_drop]
    if verbose:
        print(f"  Dropped {len(dropped)} reviewed non-brewery record(s) "
              f"({dropped['state_abbr'].value_counts().to_dict()})")

    log_filter("obdb_hygiene", "drop reviewed non-brewery records", len(df), len(kept),
               notes=f"dropped={len(dropped)}, unreviewed_candidates_kept={len(unreviewed)}")
    return kept.reset_index(drop=True)


def flag_duplicate_locations(df: pd.DataFrame, *, coord_decimals: int = 5) -> pd.DataFrame:
    """Add `dup_group` and `is_duplicate_secondary` for records that are the
    same physical location entered more than once.

    Keys on rounded coordinates (~1m at 5dp), NOT on name: same-brand records
    at genuinely different addresses are separate premises, not duplicates,
    and are deliberately left unflagged. Within a coordinate group the first
    record by OBDB id is kept and the rest marked secondary.
    """
    out = df.copy()
    # Coerce before rounding: an all-null coordinate column arrives as object
    # dtype, and Series.round then raises on the None values.
    lat_num = pd.to_numeric(out["latitude"], errors="coerce")
    lon_num = pd.to_numeric(out["longitude"], errors="coerce")
    has_coords = lat_num.notna() & lon_num.notna()
    lat = lat_num.round(coord_decimals)
    lon = lon_num.round(coord_decimals)
    # Build the key as a full-length column then mask, rather than assigning
    # into a boolean slice: when NO record has coordinates the slice is empty
    # and pandas raises "Must have equal len keys and value" on the assignment.
    out["dup_group"] = (lat.astype(str) + "," + lon.astype(str)).where(has_coords, pd.NA)

    out["is_duplicate_secondary"] = False
    grouped = out[has_coords].sort_values("id").groupby("dup_group", dropna=True)
    secondary_idx = [i for _, g in grouped if len(g) > 1 for i in g.index[1:]]
    out.loc[secondary_idx, "is_duplicate_secondary"] = True
    return out


def collapse_duplicate_locations(df: pd.DataFrame, *, verbose: bool = True) -> pd.DataFrame:
    """Drop records flagged as secondary entries for an already-counted
    physical location."""
    flagged = flag_duplicate_locations(df)
    n_dup = int(flagged["is_duplicate_secondary"].sum())
    kept = flagged[~flagged["is_duplicate_secondary"]].drop(
        columns=["dup_group", "is_duplicate_secondary"])
    if verbose:
        dropped = flagged[flagged["is_duplicate_secondary"]]
        print(f"  Collapsed {n_dup} duplicate record(s) sharing an exact coordinate with a kept record")
        for _, r in dropped.head(10).iterrows():
            print(f"    {r['name']!r} ({r['city']}, {r['state_abbr']})")
        if n_dup > 10:
            print(f"    ... and {n_dup - 10} more")
    log_filter("obdb_hygiene", "collapse same-coordinate duplicate records", len(df), len(kept),
               notes=f"dropped={n_dup}")
    return kept.reset_index(drop=True)


# Below this, a city/county disagreement is a mailing address in an
# unincorporated area and the geocode is correct; above it, the geocoder
# matched the street in the wrong place. The observed gap in this dataset runs
# from 10.8 km to 26.8 km with nothing in between, so the exact cut is not
# delicate -- see the module docstring for the two populations it separates.
MIN_MISASSIGN_KM = 25.0


def find_county_misassignments(
    df: pd.DataFrame, place_lookup: pd.DataFrame, place_geoms, *,
    min_km: float = MIN_MISASSIGN_KM, verbose: bool = True,
) -> pd.DataFrame:
    """Return the subset of `df` whose stated `city` is an incorporated place
    in the same state that does not intersect the assigned county, annotated
    with `km_from_stated_city` and `reassign_to` (a single county GEOID, or
    None when the case fails either safety condition).

    `place_lookup` columns: state_abbr, place_name_norm, county_geoids
    (frozenset -- a place can straddle a county line, hence a set).
    `place_geoms`: GeoDataFrame with state_abbr, place_name_norm, geometry,
    in an equal-area projected CRS, for the distance test.
    Both are built by `scripts/apply_obdb_hygiene.py` from cached TIGER
    polygons, so this runs offline.
    """
    import geopandas as gpd

    work = df.dropna(subset=["county_geoid", "city", "state_abbr", "latitude", "longitude"]).copy()
    work["city_norm"] = normalize_name(work["city"])

    lookup = place_lookup.set_index(["state_abbr", "place_name_norm"])["county_geoids"].to_dict()
    expected = [lookup.get((s, c)) for s, c in zip(work["state_abbr"], work["city_norm"])]
    is_mismatch = [
        geoids is not None and geoid not in geoids
        for geoids, geoid in zip(expected, work["county_geoid"])
    ]
    bad = work[is_mismatch].copy()
    bad["expected_county_geoids"] = [
        sorted(lookup.get((r.state_abbr, r.city_norm), set())) for r in bad.itertuples()
    ]
    if not len(bad):
        return bad

    # Distance from each record's own coordinates to its stated city's polygon.
    pts = gpd.GeoDataFrame(
        bad, geometry=gpd.points_from_xy(bad["longitude"], bad["latitude"]), crs="EPSG:4326",
    ).to_crs(place_geoms.crs)
    dists = []
    for row in pts.itertuples():
        poly = place_geoms[(place_geoms["state_abbr"] == row.state_abbr)
                           & (place_geoms["place_name_norm"] == row.city_norm)]
        dists.append(poly.geometry.distance(row.geometry).min() / 1000 if len(poly) else float("nan"))
    bad["km_from_stated_city"] = dists

    # Explicit object dtype: a list of None/str would otherwise become a float
    # column of NaN, and `if row.reassign_to` is TRUE for NaN -- which silently
    # reassigns every flagged record, including the mailing-address ones this
    # distance gate exists to protect.
    bad["reassign_to"] = pd.Series(
        [exp[0] if (len(exp) == 1 and pd.notna(km) and km > min_km) else None
         for exp, km in zip(bad["expected_county_geoids"], bad["km_from_stated_city"])],
        index=bad.index, dtype=object,
    )

    if verbose:
        print(f"  {len(bad)} record(s) assigned to a county that does not contain their stated city")
        for r in bad.sort_values("km_from_stated_city").itertuples():
            verdict = (f"REASSIGN -> {r.reassign_to}" if r.reassign_to is not None
                       else ("mailing address, geocode kept" if r.km_from_stated_city <= min_km
                             else f"city straddles {r.expected_county_geoids}, kept"))
            print(f"    {r.km_from_stated_city:7.1f} km  {r.name!r} ({r.city}, {r.state_abbr}) "
                  f"in {r.county_geoid} -- {verdict}")
    return bad
