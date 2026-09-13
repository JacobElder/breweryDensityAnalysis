"""Tests for src/breweries/obdb_hygiene.py -- the post-geocode record pass
that removes non-brewery contamination, collapses duplicate entries for one
physical location, and corrects county misassignments.

Each pass here encodes a judgment that is easy to regress toward something
simpler and wrong, so the tests pin the judgments, not just the plumbing:

  - a name-based non-brewery filter must NOT drop real breweries whose names
    happen to contain "Cellars"; several well-known ones do.
  - duplicate collapse must key on coordinates, never on name, or it silently
    deletes genuine satellite locations of the same brand.
  - county reassignment must fire only for far-away name collisions, never for
    a mailing address in an unincorporated area near the named city. An
    earlier revision of this module returned a float column of NaN for "do not
    reassign", and `if row.reassign_to` is True for NaN -- which reassigned
    every flagged record including all 14 mailing-address cases. That bug is
    specifically regression-tested below.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, box

from breweries import obdb_hygiene as hyg


def _records(rows: list[dict]) -> pd.DataFrame:
    """Minimal OBDB-shaped frame; every column the hygiene passes touch."""
    defaults = {"id": "x", "name": "N", "brewery_type": "micro", "city": "C",
                "state_abbr": "XX", "county_geoid": "99999", "county_name": "County",
                "latitude": 40.0, "longitude": -80.0}
    return pd.DataFrame([{**defaults, **r} for r in rows])


class TestNonBreweryNameFlagging:
    def test_winery_without_brewing_token_is_flagged(self):
        df = _records([{"name": "Heavenly Vineyards"}])
        assert hyg.flag_non_brewery_candidates(df)["non_brewery_candidate"].iloc[0]

    @pytest.mark.parametrize("name", [
        "Cellar West Artisan Ales",
        "Sapwood Cellars Brewery",
        "Sociable Cider Werks Brewing",
    ])
    def test_competing_category_with_brewing_token_is_not_flagged(self, name):
        """The regex must require the ABSENCE of a brewing token. Real
        breweries name wine/cider terms all the time."""
        df = _records([{"name": name}])
        assert not hyg.flag_non_brewery_candidates(df)["non_brewery_candidate"].iloc[0]

    def test_plain_brewery_is_untouched(self):
        df = _records([{"name": "Rhinegeist Brewery"}])
        flagged = hyg.flag_non_brewery_candidates(df)
        assert not flagged["non_brewery_candidate"].iloc[0]
        assert flagged["names_brewing"].iloc[0]

    def test_unreviewed_candidate_is_kept_not_dropped(self):
        """A name-based flag alone must never delete a record: the reviewed
        table is the only thing that drops. Otherwise a refreshed OBDB
        snapshot could silently remove a real brewery."""
        df = _records([{"name": "Some Unknown Winery", "state_abbr": "ZZ"}])
        kept = hyg.drop_reviewed_non_breweries(df, verbose=False)
        assert len(kept) == 1

    def test_reviewed_record_is_dropped(self):
        df = _records([
            {"name": "Green Bird Cellars and Organic Farms", "state_abbr": "MI"},
            {"name": "Rhinegeist Brewery", "state_abbr": "OH"},
        ])
        kept = hyg.drop_reviewed_non_breweries(df, verbose=False)
        assert list(kept["name"]) == ["Rhinegeist Brewery"]

    def test_reviewed_exclusion_is_state_specific(self):
        """Keyed on (name, state): the same name in another state is a
        different business and must not be dropped by association."""
        df = _records([{"name": "Green Bird Cellars and Organic Farms", "state_abbr": "TX"}])
        assert len(hyg.drop_reviewed_non_breweries(df, verbose=False)) == 1


class TestDuplicateCollapse:
    def test_same_coordinates_collapse_to_one(self):
        df = _records([
            {"id": "a", "name": "Peoria Artisan Brewery", "latitude": 33.676306, "longitude": -112.2},
            {"id": "b", "name": "Peoria Artisan Brewery", "latitude": 33.676306, "longitude": -112.2},
        ])
        assert len(hyg.collapse_duplicate_locations(df, verbose=False)) == 1

    def test_same_brand_different_address_is_kept(self):
        """E.J. Phair operates in Alamo, Concord and Pittsburg CA -- three
        real premises. Whether satellite taprooms should count is a
        definitional question documented elsewhere; it is NOT a duplicate,
        and collapsing on name would destroy the distinction."""
        df = _records([
            {"id": "a", "name": "E.J. Phair Brewing Co.", "latitude": 37.851053, "longitude": -122.0},
            {"id": "b", "name": "E.J. Phair Brewing Co.", "latitude": 37.978104, "longitude": -122.0},
            {"id": "c", "name": "E.J. Phair Brewing Co.", "latitude": 38.033080, "longitude": -121.9},
        ])
        assert len(hyg.collapse_duplicate_locations(df, verbose=False)) == 3

    def test_different_names_same_address_collapse(self):
        """Two brands at one address are one physical brewing location."""
        df = _records([
            {"id": "a", "name": "Grayton Beer Company", "latitude": 30.36, "longitude": -86.2},
            {"id": "b", "name": "Grayton Beer Brewpub", "latitude": 30.36, "longitude": -86.2},
        ])
        assert len(hyg.collapse_duplicate_locations(df, verbose=False)) == 1

    def test_records_without_coordinates_are_never_collapsed(self):
        df = _records([
            {"id": "a", "name": "A", "latitude": None, "longitude": None},
            {"id": "b", "name": "B", "latitude": None, "longitude": None},
        ])
        assert len(hyg.collapse_duplicate_locations(df, verbose=False)) == 2


class TestCountyMisassignment:
    """Geometry fixture: one state, two counties side by side, with the city
    of 'Farville' sitting in the RIGHT county. A record stating city=Farville
    but geocoded into the LEFT county is the misassignment signature."""

    @staticmethod
    def _fixture(record_lon: float, city_counties: frozenset[str] = frozenset({"99002"})):
        # EPSG:5070 metres. Left county spans x in [0, 100k], right [100k, 200k].
        far_city = box(150_000, 0, 160_000, 10_000)
        place_geoms = gpd.GeoDataFrame(
            {"state_abbr": ["XX"], "place_name_norm": ["farville"]},
            geometry=[far_city], crs="EPSG:5070",
        )
        # Built with an explicit object column: assigning a frozenset into a
        # DataFrame cell via .loc makes pandas try to iterate it.
        lookup = pd.DataFrame({
            "state_abbr": ["XX"], "place_name_norm": ["farville"],
            "county_geoids": pd.Series([city_counties], dtype=object),
        })
        pt = gpd.GeoSeries([Point(record_lon, 5_000)], crs="EPSG:5070").to_crs(4326).iloc[0]
        df = _records([{"name": "Test Brewing", "city": "Farville", "state_abbr": "XX",
                        "county_geoid": "99001", "latitude": pt.y, "longitude": pt.x}])
        return df, lookup, place_geoms

    def test_far_collision_is_marked_for_reassignment(self):
        # 60 km from Farville -- a street-name collision.
        df, lookup, geoms = self._fixture(record_lon=90_000)
        bad = hyg.find_county_misassignments(df, lookup, geoms, verbose=False)
        assert len(bad) == 1
        assert bad["km_from_stated_city"].iloc[0] > hyg.MIN_MISASSIGN_KM
        assert bad["reassign_to"].iloc[0] == "99002"

    def test_nearby_mailing_address_is_not_reassigned(self):
        """The regression test for the NaN-truthiness bug: a record just
        outside the city limits must come back with reassign_to = None, and
        `is not None` must be the check that keeps it."""
        df, lookup, geoms = self._fixture(record_lon=147_000)  # ~3 km away
        bad = hyg.find_county_misassignments(df, lookup, geoms, verbose=False)
        assert len(bad) == 1
        assert bad["km_from_stated_city"].iloc[0] < hyg.MIN_MISASSIGN_KM
        assert bad["reassign_to"].iloc[0] is None, "must be None, not NaN (NaN is truthy)"

    def test_ambiguous_city_spanning_two_counties_is_not_reassigned(self):
        df, lookup, geoms = self._fixture(
            record_lon=90_000, city_counties=frozenset({"99002", "99003"}))
        bad = hyg.find_county_misassignments(df, lookup, geoms, verbose=False)
        assert len(bad) == 1
        assert bad["reassign_to"].iloc[0] is None

    def test_city_matching_assigned_county_is_not_flagged(self):
        df, lookup, geoms = self._fixture(record_lon=90_000)
        df.loc[0, "county_geoid"] = "99002"  # already correct
        bad = hyg.find_county_misassignments(df, lookup, geoms, verbose=False)
        assert len(bad) == 0

    def test_unknown_city_gives_no_signal(self):
        """Most records' stated city is a neighbourhood, not an incorporated
        place (Van Nuys, La Jolla, Girdwood). Those must not be flagged."""
        df, lookup, geoms = self._fixture(record_lon=90_000)
        df.loc[0, "city"] = "Some Neighbourhood"
        bad = hyg.find_county_misassignments(df, lookup, geoms, verbose=False)
        assert len(bad) == 0


class TestNormalizeName:
    def test_strips_punctuation_and_case(self):
        got = hyg.normalize_name(pd.Series(["E.J. Phair Brewing Co.", "Taft's Ale House"]))
        assert list(got) == ["ejphairbrewingco", "taftsalehouse"]
