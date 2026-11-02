"""Load FS&N concentration facts into Postgres and build supplier -> hub edges.

Usage: uv run python scripts/build_edges.py
"""

from pathlib import Path

import polars as pl
import psycopg

from knock_on_atlas.config import get_settings
from knock_on_atlas.graph_store.edges import candidates, fact_rows, insert_facts

FSN_DIR = Path("data/processed/fsn")

rows = fact_rows(
    pl.concat(pl.read_parquet(p) for p in sorted(FSN_DIR.glob("*.parquet")))
)
url = get_settings().database_url.get_secret_value()
with psycopg.connect(url, autocommit=True) as conn:
    print(f"facts: {len(rows)} read, {insert_facts(conn, rows)} new")
picked = candidates(rows)
print(f"candidates: {len(picked)} from {picked['cik'].n_unique()} suppliers")
