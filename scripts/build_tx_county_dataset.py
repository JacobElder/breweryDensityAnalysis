"""Assemble the Texas county-level analysis dataset and run validation checkpoints 1-3.

Sixth calibration state (after NC, MI, CO, OR, and whichever of WA/GA landed
alongside this one). Texas is far larger and more heterogeneous than the other
calibration states -- 254 counties spanning huge population range -- so this
script does not force its results to resemble the smaller states; see the
build's printed output for what actually comes out.

TX liquor source: tx_liquor.py (TABC "Brewer's License" (BW) -- the modern,
post-2021-consolidation production/manufacturing license). Brewpub-only
locations (retail permit + subordinate Brewpub License (BP)) are not captured;
see tx_liquor.py's module docstring for why. Expect this to pull the TABC
capture rate below the other four calibration states, since brewpub is one of
OBDB's five included brewery_type values nationally.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from breweries.geocode import assign_geographies, fill_missing_coords
from breweries.sources import acs, cbp, obdb, osm, tx_liquor

pd.set_option("display.width", 160)

BA_TX_TOTAL_2025 = 420  # checked 2026-08-30, single state-total lookup


def build_obdb_county_counts() -> pd.DataFrame:
    df = obdb.load_state("Texas")
    df = obdb.apply_inclusion_rule(df, "TX")
    df = fill_missing_coords(df, "id", "latitude", "longitude", "address_1", "city",
                              "state_province", "postal_code", "obdb_tx")
    geo = assign_geographies(df, "latitude", "longitude", "TX", "obdb_tx")
    counts = geo.groupby("county_name", dropna=True).size().rename("obdb_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_osm_county_counts() -> pd.DataFrame:
    df = osm.load_state("TX")
    geo = assign_geographies(df, "lat", "lon", "TX", "osm_tx")
    counts = geo.groupby("county_name", dropna=True).size().rename("osm_count").reset_index()
    counts["county_name"] = counts["county_name"].str.replace(" County", "", regex=False)
    return counts


def build_cbp_county_counts() -> pd.DataFrame:
    df = cbp.load_county("TX")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "ESTAB"]].rename(columns={"ESTAB": "cbp_estab"})


def build_acs_county_denominators() -> pd.DataFrame:
    df = acs.load("TX", "county")
    df["county_name"] = df["NAME"].str.split(" County,").str[0]
    return df[["county_name", "total_population", "adults_21plus"]]


def build_liquor_county_counts() -> pd.DataFrame:
    # TABC data already carries a county field directly -- no geocoding/spatial
    # join needed (same shortcut OR's OLCC data allows).
    return tx_liquor.county_counts()


def main() -> None:
    obdb_counts = build_obdb_county_counts()
    osm_counts = build_osm_county_counts()
    cbp_counts = build_cbp_county_counts()
    acs_denom = build_acs_county_denominators()
    liquor_counts = build_liquor_county_counts()

    df = acs_denom.merge(obdb_counts, on="county_name", how="left")
    df = df.merge(osm_counts, on="county_name", how="left")
    df = df.merge(cbp_counts, on="county_name", how="left")
    df = df.merge(liquor_counts, on="county_name", how="left")

    for col in ["obdb_count", "osm_count", "cbp_estab", "liquor_count"]:
        df[col] = df[col].fillna(0).astype(int)

    df["obdb_rate_per_100k_21plus"] = df["obdb_count"] / df["adults_21plus"] * 100_000

    out_path = Path("data/processed/tx_county_analysis.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"\nWrote {out_path} ({len(df)} counties)\n")

    print("=" * 70)
    print("CHECKPOINT 1: Face validity (Austin / Travis County should rank reasonably high;")
    print("Texas is huge and diverse, so don't expect a clean top-10 the way smaller states have)")
    print("=" * 70)
    top = df[df["adults_21plus"] >= 50_000].sort_values("obdb_rate_per_100k_21plus", ascending=False)
    print(top[["county_name", "obdb_count", "adults_21plus", "obdb_rate_per_100k_21plus"]].head(15).to_string(index=False))

    travis = df[df["county_name"] == "Travis"]
    if len(travis):
        rank = (top["county_name"] == "Travis").to_numpy().nonzero()[0]
        rank_str = f"rank {rank[0] + 1} of {len(top)}" if len(rank) else "not in >=50k-adult subset"
        print(f"\nTravis County (Austin): obdb_count={travis['obdb_count'].iloc[0]}, "
              f"rate_per_100k_21plus={travis['obdb_rate_per_100k_21plus'].iloc[0]:.1f}, {rank_str}")

    print("\n" + "=" * 70)
    print("CHECKPOINT 2/3: State rollup + cross-source agreement vs BA")
    print("=" * 70)
    for label, val in [
        ("OBDB (micro/brewpub/regional/large/nano)", df["obdb_count"].sum()),
        ("OSM (craft=brewery / microbrewery=yes / pub+microbrewery)", df["osm_count"].sum()),
        ("CBP (NAICS 312120 establishments, 2023)", df["cbp_estab"].sum()),
        ("TABC Brewer's License (BW; brewpub-subordinate not captured, see tx_liquor.py)",
         df["liquor_count"].sum()),
        ("Brewers Association (2025)", BA_TX_TOTAL_2025),
    ]:
        capture = val / BA_TX_TOTAL_2025 * 100
        print(f"{label:78s} {val:5d}   ({capture:5.1f}% of BA total)")

    print("\nTop 15 counties, all four sources side by side:")
    cmp = df.sort_values("obdb_count", ascending=False).head(15)
    print(cmp[["county_name", "obdb_count", "osm_count", "cbp_estab", "liquor_count"]].to_string(index=False))


if __name__ == "__main__":
    main()
