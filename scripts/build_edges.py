"""Load FS&N concentration facts into Postgres and build supplier -> hub edges.

Usage: uv run python scripts/build_edges.py
"""

from collections import Counter
from pathlib import Path

import polars as pl
import psycopg

from knock_on_atlas.config import get_settings
from knock_on_atlas.graph_store.edges import candidates, fact_rows, insert_facts
from knock_on_atlas.graph_store.resolve import load_hubs, resolve

FSN_DIR = Path("data/processed/fsn")
HUBS = Path("data/hubs.csv")

rows = fact_rows(
    pl.concat(pl.read_parquet(p) for p in sorted(FSN_DIR.glob("*.parquet")))
)
url = get_settings().database_url.get_secret_value()
with psycopg.connect(url, autocommit=True) as conn:
    print(f"facts: {len(rows)} read, {insert_facts(conn, rows)} new")
picked = candidates(rows)
print(f"candidates: {len(picked)} from {picked['cik'].n_unique()} suppliers")

hubs = load_hubs(HUBS)
resolved = {label: resolve(label, hubs) for label in picked["member_label"].unique()}
labels = Counter(r.kind.value for r in resolved.values())
rows_by_kind = Counter(resolved[label].kind.value for label in picked["member_label"])
print(f"resolution by label: {dict(labels)}")
print(f"resolution by candidate: {dict(rows_by_kind)}")
