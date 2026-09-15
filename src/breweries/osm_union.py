"""Union OBDB with OSM-only locations, filtered so the result does not trade an
undercount for an overcount.

WHY THIS MATTERS MORE THAN THE MODEL
------------------------------------
Validated against the 18 state registries whose ground truth is actually known
(the other 5 calibrated states have documented-incomplete registries), the
OBDB-only county counts are a **median 34.6% BELOW truth**, and the fitted
model's implied counts are 33.7% below — the model is closer to truth in only
7 of 18 states, i.e. worse than a coin flip. Covariates, state effects and a
BYM2 spatial prior move accuracy against external truth by under one
percentage point, because none of them can invent a brewery that OBDB never
listed. Measurement error dominates model error by roughly 35x here.

`obdb_osm_union_additions.csv` (built by scripts/build_obdb_osm_union.py, which
does the name+distance record linkage) holds 2,984 OSM locations that matched
no OBDB record. Adding them takes national coverage from 69% to 92% of the
Brewers Association's US brewery count. That is the first-order fix.

WHY A BLANKET UNION IS WRONG
----------------------------
Adding all of them overshoots exactly where OBDB is already good. Measured
capture rate after a naive union: Colorado 0.919 -> 1.192, Oregon 0.930 ->
1.112, New Jersey 0.748 -> 1.172. A capture rate above 1.0 means the union
claims more breweries than the state's own licensee registry lists.

The mechanism is visible in the names: OSM maps satellite TAPROOMS as separate
nodes ("KettleHouse Bonner Taproom", "Myrtle Street Taproom"), and Colorado and
Oregon overshoot most precisely because they have the least real gap left for
OSM to fill, so most of what it adds is a second node for a brewery already
counted. This is the same satellite-taproom question the project already
documents for Oregon's OLCC data (methods memo Section 6), arriving through a
different door.

TWO FILTERS, AND WHAT THEY MUST NOT DO
--------------------------------------
1. TAPROOM NAMES. Must not fire on "brewpub" or "ale house" (a brewpub IS a
   brewing site) or on "growler" inside a proper noun ("Growler Bay Brewing
   Company"). The regex below is deliberately narrow for that reason: it
   matches outlet words that are not themselves brewing words.
2. BRAND ALREADY PRESENT IN THE SAME STATE. If an OSM addition's brand already
   appears in OBDB in that state, the addition is either a satellite of a
   brewery already counted, or a record-linkage miss on the same brewery.
   Either way counting it again inflates. This is the stronger filter and does
   most of the work.

Both are applied to ADDITIONS only; nothing OBDB already lists is ever removed
here. The acceptance criterion is external: no calibrated state's capture rate
may exceed 1.0 (except the five whose registries are known incomplete), which
is a ground-truth test rather than an internal one.

FUZZY BRAND MATCHING WAS TESTED AND REJECTED
--------------------------------------------
The exact brand key misses real same-brand pairs -- "Mt. Hood Brewing" vs
"mounthood", "Pelican Brewing Company - Siletz Bay" vs Pelican's Tillamook
site, "10 Barrel Brewing East Side Bend" vs its Bend pub -- so rapidfuzz
scoring against every OBDB brand in the same state was tried at thresholds
from 95 down to 75. It changes almost nothing: eligible additions 1,743
(exact) vs 1,741 (fuzzy at 95), and the overshoot set is identical at every
threshold down to 80.

That is informative rather than disappointing. It means the residual overshoot
in Colorado (1.10), Oregon (1.07) and New Jersey (1.15) is NOT same-brand
double counting -- those are locations OSM lists that the state registry does
not. Given that five other calibrated states have registries demonstrably
incomplete enough to push their raw capture ratio above 1.0 (Missouri 1.66,
Texas 1.22, Wyoming 1.29), a 7-15% excess over a licensee list is well inside
the range those registries are already known to be wrong by. Extra filtering
would be fitting noise in the reference, not removing error from the union.

So the exact-brand rule stands, and the complexity of fuzzy matching is not
carried.
"""

from __future__ import annotations

import re

import pandas as pd

# Outlet words that are NOT brewing words. Deliberately excludes "pub",
# "brewpub" and "ale house" (brewing sites), and requires "growler" to be
# followed by an outlet word so "Growler Bay Brewing Company" survives.
TAPROOM_RE = re.compile(
    r"tap\s*room\b|taproom\b|tap\s*house\b|taphouse\b|tasting\s*room\b"
    r"|bottle\s*shop\b|growler\s+(?:shop|station|fill)|beer\s*garden\b|biergarten\b",
    re.IGNORECASE,
)

# Suffixes stripped before comparing brands, so "Foo Brewing Company",
# "Foo Brewery" and "Foo Taproom" collapse to "foo".
_BRAND_SUFFIX_RE = re.compile(
    r"(brewingcompanyllc|brewingcompany|brewingco|brewworks|brewhouse|brewpub"
    r"|brewery|brewing|brewco|beercompany|beerco|beerworks|aleworks|alehouse"
    r"|taproom|taphouse|tastingroom|llc|inc)+$"
)


def brand_key(names: pd.Series) -> pd.Series:
    """Normalized brand: lowercase alphanumerics with brewing/outlet suffixes
    stripped, so a brand and its taproom compare equal.

    NEVER returns an empty string for a non-empty name. `(alt)+$` matches any
    trailing RUN of suffix tokens, so a name built entirely out of them is
    consumed whole: "Ale House Brewing Co" -- a real OBDB record in California
    -- strips to "", as do "Brewery", "Tasting Room" and "Beerworks Brewing".

    That matters because brand keys are compared as a set: every empty key in
    a state collides with every other, so one generically-named OBDB record
    would silently suppress every OSM addition in that state whose name is
    also suffix-only, with nothing logged. It happens to be harmless in the
    current snapshot (the one empty-key OBDB record is in CA and no CA
    addition strips to empty), but crowdsourced data produces placeholder
    names like "Brewery" routinely, so the next refresh could trip it.

    Falling back to the unstripped normalized name keeps such records
    comparable on their literal text instead, which is the conservative
    behaviour: "Brewery" then matches only another "Brewery".
    """
    from breweries.obdb_hygiene import normalize_name

    normalized = normalize_name(names)
    stripped = normalized.str.replace(_BRAND_SUFFIX_RE, "", regex=True)
    return stripped.where(stripped != "", normalized)


def classify_additions(additions: pd.DataFrame, obdb: pd.DataFrame) -> pd.DataFrame:
    """Flag each OSM-only location with why it would or would not be counted.

    Adds: names_taproom, brand_in_obdb_state, and `union_eligible`
    (the conjunction: a real brewing site whose brand is not already counted
    in that state).
    """
    from breweries.obdb_hygiene import flag_non_brewery_candidates

    out = flag_non_brewery_candidates(additions)
    out["names_taproom"] = out["name"].fillna("").str.contains(TAPROOM_RE)

    obdb_brands = set(zip(brand_key(obdb["name"]), obdb["state_abbr"]))
    keys = list(zip(brand_key(out["name"]), out["state_abbr"]))
    out["brand_in_obdb_state"] = [k in obdb_brands for k in keys]

    out["union_eligible"] = (
        out["names_brewing"]
        & ~out["non_brewery_candidate"]
        & ~out["names_taproom"]
        & ~out["brand_in_obdb_state"]
    )
    return out


def union_counts_by_state(
    obdb: pd.DataFrame, additions_flagged: pd.DataFrame,
) -> pd.DataFrame:
    """Per-state OBDB count, eligible additions, and the union total."""
    base = obdb["state_abbr"].value_counts().rename("obdb")
    add = (additions_flagged[additions_flagged["union_eligible"]]["state_abbr"]
           .value_counts().rename("osm_added"))
    d = pd.concat([base, add], axis=1).fillna(0).astype(int)
    d["union"] = d["obdb"] + d["osm_added"]
    return d.reset_index(names="state_abbr")
