"""Tests for src/breweries/osm_union.py -- the OBDB-union-OSM addition filter.

Pins the JUDGMENTS the module rests on, not its plumbing, because each one has
a plausible-looking wrong version:

  - a brand and its taproom must collapse to the same key, but a name built
    ENTIRELY out of suffix words must not collapse to nothing;
  - the taproom regex must not fire on brewpubs or ale houses, which are
    brewing sites, nor on "growler" inside a proper noun;
  - a brand match must be scoped to state.

The empty-brand-key case is a regression test for a real defect: `(alt)+$`
consumed "Ale House Brewing Co" (an actual OBDB record) down to "", and every
empty key in a state compares equal to every other, so one generically-named
record could silently suppress unrelated OSM additions with nothing logged.
"""

from __future__ import annotations

import pandas as pd
import pytest

from breweries.osm_union import (
    TAPROOM_RE,
    brand_key,
    classify_additions,
    union_counts_by_state,
)


class TestBrandKey:
    def test_brand_and_its_taproom_collapse_together(self):
        """The entire reason brand_key exists."""
        s = pd.Series(["Foo Brewing Company", "Foo Brewery", "Foo Taproom"])
        assert brand_key(s).nunique() == 1

    def test_llc_and_repeated_suffixes_are_stripped(self):
        assert brand_key(pd.Series(["Foo Brewing Company LLC"])).iloc[0] == "foo"
        assert brand_key(pd.Series(["Foo Brewing Brewing"])).iloc[0] == "foo"

    @pytest.mark.parametrize("name", [
        "Ale House Brewing Co",   # real OBDB record in CA -- the regression case
        "Beerworks Brewing",
        "Brewhouse Brewpub",
        "Brewery",
        "Tasting Room",
    ])
    def test_suffix_only_name_never_collapses_to_empty(self, name):
        assert brand_key(pd.Series([name])).iloc[0] != "", (
            f"{name!r} collapsed to an empty key; every empty key in a state "
            "collides with every other")

    def test_unrelated_suffix_only_names_do_not_collide(self):
        keys = brand_key(pd.Series(["Brewery", "Tasting Room", "Ale House Brewing Co"]))
        assert keys.nunique() == 3

    def test_distinct_brands_do_not_collide(self):
        assert brand_key(pd.Series(["Foo Brewing", "Bar Brewing", "Baz Taproom"])).nunique() == 3


class TestTaproomRegex:
    @pytest.mark.parametrize("name", [
        "Coldwater Mountain Brewpub",      # brewpub IS a brewing site
        "Congregation Ale House",          # so is an ale house
        "Growler Bay Brewing Company",     # "growler" inside a proper noun
        "Ale House Brewing Co",
        "Growler USA",
    ])
    def test_does_not_fire_on_brewing_sites(self, name):
        assert not TAPROOM_RE.search(name)

    @pytest.mark.parametrize("name", [
        "KettleHouse Bonner Taproom",
        "Myrtle Street Taproom",
        "Sierra Nevada Tap Room",
        "Firestone Walker Taphouse",
        "Odell Brewing Tasting Room",
        "Downtown Beer Garden",
        "Corner Growler Shop",
    ])
    def test_fires_on_genuine_outlets(self, name):
        assert TAPROOM_RE.search(name)


class TestClassifyAdditions:
    @staticmethod
    def _osm(names, states=None):
        states = states or ["CO"] * len(names)
        return pd.DataFrame({"name": names, "state_abbr": states})

    def test_brand_already_in_obdb_same_state_is_excluded(self):
        obdb = self._osm(["Foo Brewing Co"])
        out = classify_additions(self._osm(["Foo Taproom"]), obdb)
        assert out["brand_in_obdb_state"].iloc[0]
        assert not out["union_eligible"].iloc[0]

    def test_same_brand_different_state_is_kept(self):
        """A brand match is scoped to state; the same name elsewhere is a
        separately-licensed business, not a satellite."""
        obdb = self._osm(["Foo Brewing Co"], ["CO"])
        out = classify_additions(self._osm(["Foo Brewing Co"], ["OR"]), obdb)
        assert not out["brand_in_obdb_state"].iloc[0]
        assert out["union_eligible"].iloc[0]

    def test_genuinely_new_brand_is_eligible(self):
        out = classify_additions(self._osm(["Bar Brewing"]), self._osm(["Foo Brewing"]))
        assert out["union_eligible"].iloc[0]

    def test_each_filter_alone_disqualifies(self):
        obdb = pd.DataFrame({"name": [], "state_abbr": []})
        out = classify_additions(self._osm(["Some Winery", "Foo Brewing Taproom"]), obdb)
        assert not out["union_eligible"].iloc[0]   # competing beverage category
        assert not out["union_eligible"].iloc[1]   # taproom

    def test_empty_brand_key_does_not_blackhole_unrelated_additions(self):
        """Regression: 'Ale House Brewing Co' and 'Brewery' both used to strip
        to '' and therefore compare equal, wrongly marking the OSM record as an
        already-counted brand."""
        obdb = self._osm(["Ale House Brewing Co"], ["CA"])
        out = classify_additions(self._osm(["Brewery"], ["CA"]), obdb)
        assert not out["brand_in_obdb_state"].iloc[0]


class TestUnionCountsByState:
    def test_union_adds_only_eligible_records(self):
        obdb = pd.DataFrame({"name": ["A Brewing", "B Brewing"], "state_abbr": ["CO", "CO"]})
        flagged = pd.DataFrame({"state_abbr": ["CO", "CO"], "union_eligible": [True, False]})
        row = union_counts_by_state(obdb, flagged).set_index("state_abbr").loc["CO"]
        assert (row["obdb"], row["osm_added"], row["union"]) == (2, 1, 3)

    def test_state_with_no_additions_is_unchanged(self):
        obdb = pd.DataFrame({"name": ["A Brewing"], "state_abbr": ["WY"]})
        flagged = pd.DataFrame({"state_abbr": ["CO"], "union_eligible": [True]})
        d = union_counts_by_state(obdb, flagged).set_index("state_abbr")
        assert d.loc["WY", "union"] == 1
