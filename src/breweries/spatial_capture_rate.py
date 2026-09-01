"""EXPLORATORY / NOT WIRED IN: a state-adjacency-informed alternative to the
density-only pooled fallback in capture_rate_model.py.

capture_rate_model.py's pooled fallback (28 uncalibrated states) predicts a
capture rate from population density alone, ignoring which state a county is
in beyond that density number. But the 23 directly-calibrated states are not
scattered randomly across the map -- they cluster (e.g. the Northeast/
Mid-Atlantic corridor: PA, NJ, CT, NY, MA, DC, WV are all calibrated and
mostly land-adjacent to each other). That raises an obvious question: does a
state's capture rate carry information for its geographic neighbors, over and
above what density alone predicts? scripts/build_spatial_hotspots.py already
confirmed county-level brewery DENSITY is spatially autocorrelated (Moran's I
= 0.36) -- this module tests whether CAPTURE RATE (a different quantity: a
measurement-process artifact, not a brewery-prevalence signal) shows the same
pattern at the much coarser state level (23 calibrated "observations").

Two things this module provides:

1. STATE_ADJACENCY -- a hardcoded, land-border-only US state contiguity graph
   (50 states + DC). Hardcoded because state borders are static, well-known,
   non-controversial data, but verified computationally (see
   `build_adjacency_from_tiger` / `diff_adjacency`) against a Queen-contiguity
   graph built by dissolving the TIGER county polygons already used elsewhere
   in this project up to the state level. The two disagree on exactly 3 of
   112 undirected edges (IL-MI, MI-MN, NY-RI), all of which are Queen
   "vertex touch" artifacts from state boundary lines meeting at a single
   point over open water (Lake Michigan, Lake Superior, Long Island Sound
   respectively) -- NOT real land borders. STATE_ADJACENCY deliberately
   excludes those three: this module's premise is that adjacent states share
   regulatory regimes, market maturity, and cross-border commuting/brewery
   patronage, none of which a lake-corner touch point plausibly proxies for.
   (Delaware/New Jersey IS included despite being separated by the Delaware
   River, because they share a real, if tiny, land border on Artificial
   Island -- a well-known quirk of Delaware's 12-mile-circle boundary -- and
   the Queen graph confirms it independently.)

2. A simple, transparent neighbor-informed capture-rate estimator: shrinkage
   blend of (a) each state's directly-calibrated geographic neighbors' own
   empirical capture rates and (b) the existing density-only pooled
   prediction, with the blend weight increasing in the number of calibrated
   neighbors available (0 neighbors -> falls back to density-only exactly).

Deliberately NOT a formal spatial model (state-level CAR/ICAR etc.): with 23
calibrated states, a graph-based random-effects model of the kind used for
the 3,109-county CAR model (scripts/fit_spatial_car_model.py) is not
well-identified -- most states have 1-4 calibrated neighbors, several would
be islands in the calibrated-only subgraph (no calibrated neighbor at all,
e.g. TX), and a Bayesian ICAR prior over a graph that sparse mostly reduces
to reporting the prior back. See scripts/build_spatial_capture_rate_model.py
for the leave-one-state-out (LOSO) validation this recommendation rests on,
and its printed honest verdict on whether the neighbor signal actually beats
the existing density-only pooled baseline out of sample.

NOT wired into capture_rate_model.py / apply_correction() -- this is a
validation/prototype deliverable. See scripts/build_spatial_capture_rate_model.py
for the LOSO comparison this module's approach is judged by.
"""

from __future__ import annotations

from typing import Mapping

# ---------------------------------------------------------------------------
# US state (+ DC) land-border contiguity graph.
# ---------------------------------------------------------------------------
# Hardcoded (static, well-known data -- see module docstring for why this is
# reasonable here, unlike everything else in this project) but verified
# against an independently computed TIGER-dissolve Queen-contiguity graph in
# build_adjacency_from_tiger()/diff_adjacency() below; see
# scripts/build_spatial_capture_rate_model.py for the verification run.
#
# AK and HI have no land neighbors (not land-contiguous with any other
# state) -- both map to an empty neighbor list, so any state-adjacency-based
# estimate for them has nothing to borrow from and callers should fall back
# to the existing density-only pooled estimate for them (see
# neighbor_average_capture_rate below, which returns None in that case).
STATE_ADJACENCY: dict[str, list[str]] = {
    "AL": ["FL", "GA", "MS", "TN"],
    "AK": [],
    "AZ": ["CA", "CO", "NM", "NV", "UT"],
    "AR": ["LA", "MO", "MS", "OK", "TN", "TX"],
    "CA": ["AZ", "NV", "OR"],
    "CO": ["AZ", "KS", "NE", "NM", "OK", "UT", "WY"],
    "CT": ["MA", "NY", "RI"],
    "DE": ["MD", "NJ", "PA"],
    "DC": ["MD", "VA"],
    "FL": ["AL", "GA"],
    "GA": ["AL", "FL", "NC", "SC", "TN"],
    "HI": [],
    "ID": ["MT", "NV", "OR", "UT", "WA", "WY"],
    "IL": ["IA", "IN", "KY", "MO", "WI"],
    "IN": ["IL", "KY", "MI", "OH"],
    "IA": ["IL", "MN", "MO", "NE", "SD", "WI"],
    "KS": ["CO", "MO", "NE", "OK"],
    "KY": ["IL", "IN", "MO", "OH", "TN", "VA", "WV"],
    "LA": ["AR", "MS", "TX"],
    "ME": ["NH"],
    "MD": ["DC", "DE", "PA", "VA", "WV"],
    "MA": ["CT", "NH", "NY", "RI", "VT"],
    "MI": ["IN", "OH", "WI"],
    "MN": ["IA", "ND", "SD", "WI"],
    "MS": ["AL", "AR", "LA", "TN"],
    "MO": ["AR", "IA", "IL", "KS", "KY", "NE", "OK", "TN"],
    "MT": ["ID", "ND", "SD", "WY"],
    "NE": ["CO", "IA", "KS", "MO", "SD", "WY"],
    "NV": ["AZ", "CA", "ID", "OR", "UT"],
    "NH": ["MA", "ME", "VT"],
    "NJ": ["DE", "NY", "PA"],
    "NM": ["AZ", "CO", "OK", "TX", "UT"],
    "NY": ["CT", "MA", "NJ", "PA", "VT"],
    "NC": ["GA", "SC", "TN", "VA"],
    "ND": ["MN", "MT", "SD"],
    "OH": ["IN", "KY", "MI", "PA", "WV"],
    "OK": ["AR", "CO", "KS", "MO", "NM", "TX"],
    "OR": ["CA", "ID", "NV", "WA"],
    "PA": ["DE", "MD", "NJ", "NY", "OH", "WV"],
    "RI": ["CT", "MA"],
    "SC": ["GA", "NC"],
    "SD": ["IA", "MN", "MT", "NE", "ND", "WY"],
    "TN": ["AL", "AR", "GA", "KY", "MS", "MO", "NC", "VA"],
    "TX": ["AR", "LA", "NM", "OK"],
    "UT": ["AZ", "CO", "ID", "NV", "NM", "WY"],
    "VT": ["MA", "NH", "NY"],
    "VA": ["DC", "KY", "MD", "NC", "TN", "WV"],
    "WA": ["ID", "OR"],
    "WV": ["KY", "MD", "OH", "PA", "VA"],
    "WI": ["IL", "IA", "MI", "MN"],
    "WY": ["CO", "ID", "MT", "NE", "SD", "UT"],
}


def _check_symmetric(adjacency: Mapping[str, list[str]]) -> list[tuple[str, str]]:
    """Return any (a, b) pairs where a lists b as a neighbor but b doesn't list a
    back -- a state border graph must be symmetric by construction, so any
    asymmetry here indicates a typo in STATE_ADJACENCY."""
    problems = []
    for a, neighbors in adjacency.items():
        for b in neighbors:
            if a not in adjacency.get(b, []):
                problems.append((a, b))
    return problems


_asymmetries = _check_symmetric(STATE_ADJACENCY)
if _asymmetries:
    raise AssertionError(f"STATE_ADJACENCY is not symmetric: {_asymmetries}")


def edge_count(adjacency: Mapping[str, list[str]] = STATE_ADJACENCY) -> int:
    """Total undirected edges in the graph (each land border counted once)."""
    return sum(len(v) for v in adjacency.values()) // 2


# ---------------------------------------------------------------------------
# Computational verification against TIGER county polygons.
# ---------------------------------------------------------------------------

def build_adjacency_from_tiger() -> dict[str, list[str]]:
    """Independently derive a state contiguity graph from the TIGER county
    polygons already cached by breweries.sources.tiger: dissolve counties up
    to state polygons (by STATEFP), then compute Queen contiguity (shares an
    edge OR a vertex -- same convention used in build_spatial_hotspots.py and
    fit_spatial_car_model.py) over the 56 state/territory polygons.

    Heavy geospatial imports (geopandas, libpysal) are done lazily inside this
    function rather than at module level, since STATE_ADJACENCY above is the
    fast path everything else in this module uses -- this function exists
    purely for the one-time verification run in
    scripts/build_spatial_capture_rate_model.py.
    """
    import warnings

    from libpysal.weights import Queen

    from breweries.sources import tiger
    from breweries.state_fips import STATE_FIPS_ALL

    counties = tiger.load_counties()[["STATEFP", "geometry"]]
    states = counties.dissolve(by="STATEFP").reset_index()
    states = states.to_crs(epsg=5070)  # CONUS Albers Equal Area (fine for a contiguity check even incl. AK/HI)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # libpysal warns about islands (AK, HI, territories) -- expected here
        w = Queen.from_dataframe(states, use_index=False)

    fips_to_abbr = {v: k for k, v in STATE_FIPS_ALL.items()}
    adjacency: dict[str, list[str]] = {}
    for i, neighbor_idx in w.neighbors.items():
        fips_i = states.loc[i, "STATEFP"]
        abbr_i = fips_to_abbr.get(fips_i)
        if abbr_i is None:
            continue  # non-state territory (PR, island areas) -- not part of this project's state list
        nbr_abbrs = sorted(
            fips_to_abbr[states.loc[j, "STATEFP"]]
            for j in neighbor_idx
            if states.loc[j, "STATEFP"] in fips_to_abbr
        )
        adjacency[abbr_i] = nbr_abbrs
    # Islands (no Queen neighbors at all): AK, HI, and non-state territories.
    for i in w.islands:
        fips_i = states.loc[i, "STATEFP"]
        abbr_i = fips_to_abbr.get(fips_i)
        if abbr_i is not None:
            adjacency[abbr_i] = []
    return adjacency


def diff_adjacency(
    computed: Mapping[str, list[str]], hardcoded: Mapping[str, list[str]] = STATE_ADJACENCY
) -> dict[str, list[tuple[str, str]]]:
    """Compare a computed adjacency graph (e.g. from build_adjacency_from_tiger())
    against the hardcoded STATE_ADJACENCY. Returns {'computed_only': [...],
    'hardcoded_only': [...]} lists of (state, neighbor) edges present in one
    graph but not the other (each undirected edge reported once)."""
    def edge_set(g: Mapping[str, list[str]]) -> set[tuple[str, str]]:
        edges = set()
        for a, neighbors in g.items():
            for b in neighbors:
                edges.add(tuple(sorted((a, b))))
        return edges

    computed_edges = edge_set(computed)
    hardcoded_edges = edge_set(hardcoded)
    return {
        "computed_only": sorted(computed_edges - hardcoded_edges),
        "hardcoded_only": sorted(hardcoded_edges - computed_edges),
    }


# ---------------------------------------------------------------------------
# Neighbor-informed capture-rate estimate.
# ---------------------------------------------------------------------------

# Shrinkage constant for the neighbor/density blend weight w = n / (n + K),
# where n = number of directly-calibrated geographic neighbors. Chosen by grid
# search over LOSO error in scripts/build_spatial_capture_rate_model.py (see
# that script's printed grid-search table) -- NOT an arbitrary default. A
# small K means even 1 calibrated neighbor gets substantial weight; a large K
# means the blend barely moves off the density-only baseline until many
# neighbors are calibrated. See that script's LOSO section for the honest
# comparison this constant is chosen against, and for how sensitive the result
# is to it.
DEFAULT_SHRINKAGE_K = 2.0


def calibrated_neighbor_rates(
    state: str,
    calibrated_rates: Mapping[str, float],
    adjacency: Mapping[str, list[str]] = STATE_ADJACENCY,
) -> list[float]:
    """Capture rates of `state`'s directly-calibrated geographic neighbors
    (excludes `state` itself by construction -- a state is never its own
    neighbor). `calibrated_rates` should already be clipped at 1.0 (a capture
    rate cannot exceed 1.0 by definition) if it's meant to be compared
    apples-to-apples with capture_rate_model.py's clipped values -- this
    function does not clip on its own, it just looks up whatever is passed.
    """
    neighbors = adjacency.get(state, [])
    return [calibrated_rates[n] for n in neighbors if n in calibrated_rates]


def neighbor_average_capture_rate(
    state: str,
    calibrated_rates: Mapping[str, float],
    adjacency: Mapping[str, list[str]] = STATE_ADJACENCY,
) -> float | None:
    """Simple mean of `state`'s directly-calibrated neighbors' capture rates,
    or None if `state` has zero calibrated neighbors (e.g. TX among the 23
    calibration states, or AK/HI which have zero neighbors of any kind) --
    callers should fall back to the density-only pooled estimate in that
    case, there is nothing to borrow from."""
    rates = calibrated_neighbor_rates(state, calibrated_rates, adjacency)
    if not rates:
        return None
    return sum(rates) / len(rates)


def blended_capture_rate(
    state: str,
    calibrated_rates: Mapping[str, float],
    density_only_rate: float,
    adjacency: Mapping[str, list[str]] = STATE_ADJACENCY,
    k: float = DEFAULT_SHRINKAGE_K,
) -> dict:
    """Neighbor-informed capture-rate estimate for an uncalibrated state:
    shrinkage blend of the directly-calibrated-neighbor average and the
    existing density-only pooled estimate, weighted by how much calibrated
    neighbor evidence is available.

        w = n / (n + k)          n = number of directly-calibrated neighbors
        rate = w * neighbor_avg + (1 - w) * density_only_rate

    n=0 (no calibrated neighbors) collapses exactly to density_only_rate
    (w=0), so this is a strict superset of the existing pooled fallback, not
    a replacement that could do worse for a totally isolated state.

    `density_only_rate` is supplied by the caller (this module deliberately
    does not import or duplicate capture_rate_model.py's pooled-model fitting
    logic -- see scripts/build_spatial_capture_rate_model.py for how it's
    computed identically to the production model, including in each LOSO
    fold).
    """
    neighbor_rates = calibrated_neighbor_rates(state, calibrated_rates, adjacency)
    n = len(neighbor_rates)
    if n == 0:
        return {
            "capture_rate": density_only_rate,
            "source": "density_only_no_calibrated_neighbors",
            "n_calibrated_neighbors": 0,
            "neighbor_avg_rate": None,
            "density_only_rate": density_only_rate,
            "blend_weight": 0.0,
        }
    neighbor_avg = sum(neighbor_rates) / n
    w = n / (n + k)
    blended = w * neighbor_avg + (1 - w) * density_only_rate
    return {
        "capture_rate": min(blended, 1.0),  # a capture rate cannot exceed 1.0 by definition
        "source": "neighbor_density_blend",
        "n_calibrated_neighbors": n,
        "neighbor_avg_rate": neighbor_avg,
        "density_only_rate": density_only_rate,
        "blend_weight": w,
    }
