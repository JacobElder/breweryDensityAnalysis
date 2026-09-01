"""Record-level matching between two independent brewery lists, and a
capture-recapture (Lincoln-Petersen / Chapman) estimate of the true population.

OBDB and OSM are used as the two "lists" here rather than OBDB vs. a state ABC
roster: NC ABC's individual-permit search sits behind a search form that
returned server errors to a scripted session (a genuine 500, not a bot-detection
challenge — no Cloudflare/CAPTCHA page was served) and only the county-level
aggregate report was retrievable. OBDB and OSM are both record-level, already in
hand, and the project handoff itself frames their errors as uncorrelated, which
is the condition capture-recapture needs. NC ABC and Brewers Association state
totals are used as an external check on the resulting estimate, not as one of
the two matched lists.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
from rapidfuzz import fuzz
from scipy.optimize import linear_sum_assignment

EARTH_RADIUS_M = 6_371_000

_SUFFIX_RE = re.compile(
    r"\b(brewing( company| co)?|brewery|beer( company| co)?|company|ales?|taproom|llc|inc|co\.?)\b",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^a-z0-9\s]")
# Compound/dual-brand listings (a brewpub-and-brewer sharing one building, a
# renamed brewery whose old and new names both got entered) show up in OBDB as
# a single "X / Y" record — e.g. "Automatic Brewing Co. / Blind Lady Alehouse".
# The other source usually lists this as one single-brand record ("Blind Lady
# Ale House"), so matching the compound string as a whole against a single
# brand name scores low even though it's the same physical brewery.
_SLASH_SPLIT_RE = re.compile(r"\s*/\s*")


def normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    s = name.lower()
    s = _SUFFIX_RE.sub("", s)
    s = _PUNCT_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def name_variants(name: str) -> list[str]:
    """Normalized name candidates to match against: the whole name, plus (for
    compound "X / Y" names) each slash-delimited sub-name normalized on its
    own. This is general-purpose — it doesn't special-case any one brewery —
    and lets a single-brand record match whichever half of a compound record
    names the same brand.
    """
    if not isinstance(name, str):
        return [""]
    variants = [normalize_name(name)]
    parts = _SLASH_SPLIT_RE.split(name)
    if len(parts) > 1:
        variants.extend(normalize_name(p) for p in parts)
    seen: set[str] = set()
    out = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out or [""]


def _name_score(variants_a: list[str], variants_b: list[str]) -> float:
    """Best match score across every (sub-name) pair, so a compound name only
    needs ONE of its parts to match well.

    name_variants() always puts the whole-name normalization first (index 0);
    any later entries come from splitting a compound "X / Y" name. The
    whole-vs-whole comparison uses token_sort_ratio alone, exactly as before
    non-compound-name matching is unaffected. Any comparison touching a
    split sub-name additionally tries token_set_ratio and takes the better of
    the two: splitting a compound name creates a token-COUNT mismatch (one
    side now has fewer words) that token_sort_ratio penalizes on its own, but
    token_set_ratio is built for exactly that extra/missing-token case. E.g.
    "Blind Lady Alehouse" (split from "Automatic Brewing Co. / Blind Lady
    Alehouse") vs. single-brand OSM "Blind Lady Ale House" scores 63 by
    token_sort_ratio alone (< the 65 default threshold) but 91 once
    token_set_ratio is also tried.
    """
    whole_a, whole_b = variants_a[0], variants_b[0]
    best = fuzz.token_sort_ratio(whole_a, whole_b) if whole_a and whole_b else 0.0

    if len(variants_a) == 1 and len(variants_b) == 1:
        return best  # neither name was compound; nothing more to try

    for va in variants_a:
        for vb in variants_b:
            if va == whole_a and vb == whole_b:
                continue  # already scored above
            if not va or not vb:
                continue
            score = max(fuzz.token_sort_ratio(va, vb), fuzz.token_set_ratio(va, vb))
            if score > best:
                best = score
    return best


def _haversine_m(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(a))


# SENTINEL: cost for "not a usable candidate pair" (too far or too
# dissimilar) in the Hungarian assignment's cost matrix; never preferred
# over a real candidate, and any pairing still stuck with it after solving
# (e.g. more rows than columns) is discarded. Shared by every assignment
# pass (primary + fallback) so they behave identically.
_SENTINEL = 1e6


def _assign_pass(
    dist: np.ndarray,
    variants_a: list[list[str]],
    variants_b: list[list[str]],
    avail_rows: np.ndarray,
    avail_cols: np.ndarray,
    max_distance_m: float,
    name_threshold: float,
) -> list[tuple[int, int, float, float]]:
    """Run one Hungarian-algorithm 1:1 assignment pass, restricted to
    avail_rows x avail_cols (global row/col indices not yet matched by an
    earlier pass), using the given (max_distance_m, name_threshold).

    Returns a list of (row_index, col_index, name_score, distance_m) for
    accepted matches only. This is the same optimal-assignment machinery
    match_records has always used for its single pass; factoring it out
    lets match_records run it more than once with progressively looser
    settings (see match_records's fallback_stages) without duplicating the
    sentinel-cost-matrix logic.
    """
    if len(avail_rows) == 0 or len(avail_cols) == 0:
        return []

    sub_dist = dist[np.ix_(avail_rows, avail_cols)]
    within = sub_dist <= max_distance_m
    if not within.any():
        return []

    # Restrict further to rows/cols that have at least one within-distance
    # candidate -- keeps the cost matrix (and the Hungarian algorithm's
    # O(k^3) cost) tied to the number of records actually near each other.
    keep_rows = within.any(axis=1)
    keep_cols = within.any(axis=0)
    cand_rows = avail_rows[keep_rows]
    cand_cols = avail_cols[keep_cols]
    sub_within = within[np.ix_(keep_rows, keep_cols)]

    # Sub-threshold pairs are excluded from the cost matrix outright (as
    # _SENTINEL), not merely filtered out of the result afterward: if a
    # below-threshold pair were given its real (better-than-sentinel) cost,
    # the optimizer could "spend" a good match's slot trying to free up a
    # column for a pairing that was never going to be accepted anyway,
    # producing a worse final result than the pre-threshold optimum. Making
    # every sub-threshold pair equally unattractive (_SENTINEL) means the
    # optimizer only ever trades a real match away for a BETTER real match.
    sub_cost = np.full((len(cand_rows), len(cand_cols)), _SENTINEL)
    for ii, jj in zip(*np.where(sub_within)):
        i, j = cand_rows[ii], cand_cols[jj]
        score = _name_score(variants_a[i], variants_b[j])
        if score >= name_threshold:
            sub_cost[ii, jj] = -score  # linear_sum_assignment minimizes cost

    row_ind, col_ind = linear_sum_assignment(sub_cost)
    results = []
    for ri, ci in zip(row_ind, col_ind):
        cost = sub_cost[ri, ci]
        if cost >= _SENTINEL:
            continue  # forced pairing with a non-candidate; discard
        i, j = cand_rows[ri], cand_cols[ci]
        results.append((int(i), int(j), -cost, dist[i, j]))
    return results


def match_records(
    df_a: pd.DataFrame, df_b: pd.DataFrame,
    name_a: str, name_b: str, lat_a: str, lon_a: str, lat_b: str, lon_b: str,
    max_distance_m: float = 300, name_threshold: float = 65,
    fallback_stages: list[tuple[float, float]] | None = None,
) -> pd.DataFrame:
    """Optimal (Hungarian-algorithm) 1:1 pairing within max_distance_m.

    Each row in df_a is matched to at most one row in df_b, and vice versa.
    Candidate pairs (within max_distance_m, using haversine distance) are
    scored by rapidfuzz name similarity on normalized names -- including, for
    compound "X / Y" names, each slash-delimited sub-name scored
    independently (see name_variants()) -- and then assigned globally via
    scipy.optimize.linear_sum_assignment (a.k.a. the Hungarian algorithm),
    which finds the assignment maximizing total match quality across ALL
    pairs at once. This replaces an earlier greedy nearest-then-best-name-match
    approach (process df_a in order, first-match-wins) that could let one
    df_a record "steal" the correct match slot of another -- e.g. if df_a
    records i and i' are both near-candidates for df_b records j and j', the
    greedy version could lock in i-j even when the globally best pairing is
    i-j' and i'-j. The optimizer only ever considers pairs that are within
    max_distance_m; a pair below max_distance_m or name_threshold is never
    assigned, even if the optimizer would otherwise prefer it -- those
    thresholds are unchanged, only how candidates are ASSIGNED changed.

    fallback_stages (opt-in; None by default so existing callers/tests see
    byte-identical behavior) lets records still unmatched after the primary
    (max_distance_m, name_threshold) pass get a second chance under
    DIFFERENT, still-strict (radius, name_threshold) combinations, applied
    in the given order -- each stage only considers rows/cols left unmatched
    by every earlier stage, and each stage is itself solved as its own
    optimal Hungarian assignment (not greedily), so the same "no stealing a
    better match's slot" guarantee holds within every stage. This exists to
    catch two documented false-negative causes that a single (radius,
    threshold) pair cannot cover at once, since they pull in opposite
    directions:

    1. Coordinate mismatches: OBDB's address-geocoded point and OSM's mapped
       node for the SAME real brewery can legitimately be several km apart
       (bad/stale geocode, building footprint vs. entrance node, etc.), so a
       tight max_distance_m never lets them pair even though the name is an
       exact/near-exact match (e.g. "Cellarmaker Brewing Company" (OBDB) vs.
       "Cellarmaker" (OSM), ~3.6km apart in San Francisco -- both normalize
       to "cellarmaker", a 100 name score). A wide radius is safe here
       specifically BECAUSE the name match is so strong: two DIFFERENT
       breweries sharing a near-identical name within the same state is
       vanishingly unlikely, so requiring name_score >= 90 (a "near-exact
       name" bar, not just "similar-ish") lets the radius open up to
       state-scale (~5000m) without meaningfully raising false-match risk.
    2. Near-threshold rename/abbreviation pairs: a genuine rename or a
       shortened public-facing name (e.g. OBDB's "Wild Heaven Craft Beers"
       vs. OSM's "Wild Heaven Beer", 64.7 -- just under the 65 default) can
       land a point or two under name_threshold while the two records sit
       essentially on top of each other (that pair is ~63m apart). Here the
       tight distance is what justifies accepting a lower name score: at a
       radius far tighter than max_distance_m, two DIFFERENT breweries
       happening to occupy virtually the same point AND share overlapping
       name tokens is also vanishingly unlikely, so relaxing name_threshold
       (while keeping the radius tight, e.g. 150m) is safe even though the
       score alone wouldn't clear the primary bar.

    Each stage therefore trades ONE constraint for slack while keeping the
    OTHER constraint tight, rather than loosening both at once (which would
    reopen the false-match risk the primary pass's thresholds exist to
    prevent). A stage that only reintroduces already-covered pairs (radius
    and threshold both looser than the primary pass) is not a safe
    trade and should not be added.

    A third documented false-negative cause -- OBDB records with no street
    address at all, so geocoding can never populate their coordinates -- is
    NOT addressed by fallback_stages (or by any radius/threshold trade at
    all): with no valid df_b coordinate, distance to every df_a record is
    inf, so no (radius, threshold) combination can ever produce a candidate
    pair for that record. Fixing that class requires giving those records
    coordinates in the first place, not adjusting how matches are scored.

    Returns one row per df_a record with the matched df_b index (-1 if unmatched) and
    the match name-similarity score, so match quality can be inspected/audited
    rather than trusted blindly.
    """
    a = df_a.reset_index(drop=True).copy()
    b = df_b.reset_index(drop=True).copy()

    n, m = len(a), len(b)
    match_idx = np.full(n, -1, dtype=int)
    match_score = np.full(n, np.nan)
    match_dist = np.full(n, np.nan)

    if n == 0 or m == 0:
        a["matched_b_index"] = match_idx
        a["match_name_score"] = match_score
        a["match_distance_m"] = match_dist
        return a

    variants_a = a[name_a].map(name_variants).tolist()
    variants_b = b[name_b].map(name_variants).tolist()

    lat_a_vals = a[lat_a].to_numpy(dtype=float)
    lon_a_vals = a[lon_a].to_numpy(dtype=float)
    lat_b_vals = b[lat_b].to_numpy(dtype=float)
    lon_b_vals = b[lon_b].to_numpy(dtype=float)

    valid_a = ~(np.isnan(lat_a_vals) | np.isnan(lon_a_vals))
    valid_b = ~(np.isnan(lat_b_vals) | np.isnan(lon_b_vals))

    # Pairwise haversine distance, vectorized (broadcast); NaN coords -> inf
    # distance so they never form a candidate pair.
    dist = np.full((n, m), np.inf)
    if valid_a.any() and valid_b.any():
        ia = np.where(valid_a)[0]
        ib = np.where(valid_b)[0]
        d = _haversine_m(
            lat_a_vals[ia][:, None], lon_a_vals[ia][:, None],
            lat_b_vals[ib][None, :], lon_b_vals[ib][None, :],
        )
        dist[np.ix_(ia, ib)] = d

    def _apply(results: list[tuple[int, int, float, float]]) -> None:
        for i, j, score, d in results:
            match_idx[i] = j
            match_score[i] = score
            match_dist[i] = d

    _apply(_assign_pass(
        dist, variants_a, variants_b,
        np.arange(n), np.arange(m),
        max_distance_m, name_threshold,
    ))

    for stage_max_distance_m, stage_name_threshold in (fallback_stages or []):
        avail_rows = np.where(match_idx < 0)[0]
        if len(avail_rows) == 0:
            break
        used_cols = set(match_idx[match_idx >= 0].tolist())
        avail_cols = np.array(
            [j for j in range(m) if j not in used_cols], dtype=int
        )
        _apply(_assign_pass(
            dist, variants_a, variants_b,
            avail_rows, avail_cols,
            stage_max_distance_m, stage_name_threshold,
        ))

    a["matched_b_index"] = match_idx
    a["match_name_score"] = match_score
    a["match_distance_m"] = match_dist
    return a


def lincoln_petersen(n1: int, n2: int, m: int) -> dict:
    """Chapman's bias-corrected capture-recapture estimator with a normal-approx CI.

    n1, n2: sizes of the two independent lists. m: count found in both.
    """
    n_hat = ((n1 + 1) * (n2 + 1)) / (m + 1) - 1
    var = ((n1 + 1) * (n2 + 1) * (n1 - m) * (n2 - m)) / ((m + 1) ** 2 * (m + 2))
    se = np.sqrt(var)
    return {
        "n1": n1, "n2": n2, "m": m,
        "n_hat": n_hat,
        "se": se,
        "ci_low": n_hat - 1.96 * se,
        "ci_high": n_hat + 1.96 * se,
        "capture_rate_1": n1 / n_hat,
        "capture_rate_2": n2 / n_hat,
    }
