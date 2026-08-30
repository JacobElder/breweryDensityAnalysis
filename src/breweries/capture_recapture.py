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

EARTH_RADIUS_M = 6_371_000

_SUFFIX_RE = re.compile(
    r"\b(brewing( company| co)?|brewery|beer( co)?|ales?|taproom|llc|inc|co\.?)\b",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^a-z0-9\s]")


def normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    s = name.lower()
    s = _SUFFIX_RE.sub("", s)
    s = _PUNCT_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _haversine_m(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(a))


def match_records(
    df_a: pd.DataFrame, df_b: pd.DataFrame,
    name_a: str, name_b: str, lat_a: str, lon_a: str, lat_b: str, lon_b: str,
    max_distance_m: float = 300, name_threshold: float = 65,
) -> pd.DataFrame:
    """Greedy nearest-then-best-name-match pairing within max_distance_m.

    Each row in df_a is matched to at most one row in df_b, and vice versa.
    Returns one row per df_a record with the matched df_b index (or NaN) and
    the match name-similarity score, so match quality can be inspected/audited
    rather than trusted blindly.
    """
    a = df_a.reset_index(drop=True).copy()
    b = df_b.reset_index(drop=True).copy()
    a["_norm_name"] = a[name_a].map(normalize_name)
    b["_norm_name"] = b[name_b].map(normalize_name)

    used_b = set()
    match_idx = np.full(len(a), -1, dtype=int)
    match_score = np.full(len(a), np.nan)
    match_dist = np.full(len(a), np.nan)

    for i in range(len(a)):
        lat_i, lon_i = a.loc[i, lat_a], a.loc[i, lon_a]
        if pd.isna(lat_i) or pd.isna(lon_i):
            continue
        dists = _haversine_m(lat_i, lon_i, b[lat_b].values, b[lon_b].values)
        nearby = np.where(dists <= max_distance_m)[0]
        nearby = [j for j in nearby if j not in used_b]
        if len(nearby) == 0:
            continue

        best_j, best_score = None, -1
        for j in nearby:
            score = fuzz.token_sort_ratio(a.loc[i, "_norm_name"], b.loc[j, "_norm_name"])
            if score > best_score:
                best_j, best_score = j, score

        if best_score >= name_threshold:
            match_idx[i] = best_j
            match_score[i] = best_score
            match_dist[i] = dists[best_j]
            used_b.add(best_j)

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
