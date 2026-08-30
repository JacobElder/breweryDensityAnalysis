"""Append-only manifest of every fetch and filter step, for reproducibility auditing.

Every raw data pull and every row-dropping filter must call log_fetch / log_filter
so the pipeline never silently drops rows. Entries are appended as JSON lines to
data/raw/manifest.jsonl and never overwritten.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_PATH = Path("data/raw/manifest.jsonl")


def _append(entry: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(), **entry}
    with MANIFEST_PATH.open("a") as f:
        f.write(json.dumps(entry, default=int) + "\n")


def log_fetch(source: str, url: str, dest_path: str, row_count: int | None = None, notes: str = "") -> None:
    """Record a raw data pull: where it came from, where it's cached, and when."""
    _append(
        {
            "event": "fetch",
            "source": source,
            "url": url,
            "dest_path": dest_path,
            "row_count": row_count,
            "notes": notes,
        }
    )


def log_filter(source: str, step: str, before: int, after: int, notes: str = "") -> None:
    """Record a filter/dedup step with before/after row counts. Never silently drop rows."""
    _append(
        {
            "event": "filter",
            "source": source,
            "step": step,
            "rows_before": before,
            "rows_after": after,
            "rows_dropped": before - after,
            "notes": notes,
        }
    )


def read_manifest() -> list[dict]:
    if not MANIFEST_PATH.exists():
        return []
    with MANIFEST_PATH.open() as f:
        return [json.loads(line) for line in f if line.strip()]
