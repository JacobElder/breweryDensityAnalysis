"""Free, keyless address-to-coordinate geocoding via the Census Bureau's Geocoder batch API.

Used only as a fallback for records missing lat/lon (e.g. ~32% of NC OBDB rows
have a complete street address but no coordinates). Matched coordinates are fed
back through the same TIGER spatial join as directly-coordinate records, so
place/county/CBSA assignment uses one consistent method regardless of source.
"""

from __future__ import annotations

import io

import pandas as pd
import requests

BATCH_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
BENCHMARK = "Public_AR_Current"
MAX_BATCH_SIZE = 10000  # Census Geocoder batch API limit per request


def geocode_addresses(df: pd.DataFrame, id_col: str, street_col: str, city_col: str,
                       state_col: str, zip_col: str | None = None) -> pd.DataFrame:
    """Batch-geocode addresses, returning matched lon/lat keyed by id_col.

    Unmatched rows are returned with null coordinates, never dropped — the caller
    logs the before/after match count.
    """
    batch = pd.DataFrame({
        "id": df[id_col],
        "street": df[street_col].fillna(""),
        "city": df[city_col].fillna(""),
        "state": df[state_col].fillna(""),
        "zip": df[zip_col].fillna("") if zip_col else "",
    })

    results = []
    for start in range(0, len(batch), MAX_BATCH_SIZE):
        chunk = batch.iloc[start:start + MAX_BATCH_SIZE]
        csv_buf = io.StringIO()
        chunk.to_csv(csv_buf, header=False, index=False)
        files = {"addressFile": ("addresses.csv", csv_buf.getvalue(), "text/csv")}
        data = {"benchmark": BENCHMARK}
        resp = requests.post(BATCH_URL, files=files, data=data, timeout=300)
        resp.raise_for_status()

        cols = ["id", "input_address", "match_indicator", "match_type",
                "matched_address", "coordinates", "tiger_line_id", "side"]
        result = pd.read_csv(io.StringIO(resp.text), header=None, names=cols, dtype=str)
        results.append(result)

    out = pd.concat(results, ignore_index=True)
    coords = out["coordinates"].str.split(",", expand=True)
    out["geocoded_lon"] = pd.to_numeric(coords[0], errors="coerce")
    out["geocoded_lat"] = pd.to_numeric(coords[1], errors="coerce")
    out["id"] = out["id"].astype(df[id_col].dtype)

    return out[["id", "match_indicator", "geocoded_lat", "geocoded_lon"]].rename(columns={"id": id_col})
