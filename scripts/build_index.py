"""Embed new chunks, rebuild the BM25 index, and record corpus and database sizes.

Writes evals/index/<UTC timestamp>.json.

Usage: uv run python scripts/build_index.py
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from knock_on_atlas.config import get_settings
from knock_on_atlas.retrieval.bm25 import build_index
from knock_on_atlas.retrieval.embed import BgeEmbedder, embed_missing

SUMMARY_DIR = Path("evals/index")
# Neon's free plan gives 0.5 GB; the kickoff caps the database at 400 MB.
DB_LIMIT_BYTES = 400 * 1024 * 1024
TABLES = ["documents", "document_chunks", "bm25_index", "entity_edges", "jev_calls"]

url = get_settings().database_url.get_secret_value()
with psycopg.connect(url, autocommit=True) as conn:
    embedded = embed_missing(conn, BgeEmbedder())
    indexed = build_index(conn)
    by_section = conn.execute(
        "SELECT d.form || '/' || d.section, count(*) FROM document_chunks c"
        " JOIN documents d ON d.id = c.document_id GROUP BY 1 ORDER BY 1"
    ).fetchall()
    companies, filings, sections = conn.execute(
        "SELECT count(DISTINCT cik), count(DISTINCT accession), count(*) FROM documents"
    ).fetchone() or (0, 0, 0)
    sizes = {
        table: conn.execute(
            "SELECT pg_total_relation_size(%s::regclass)", (table,)
        ).fetchone()[0]
        for table in TABLES
    }
    db_bytes = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]

built_at = datetime.now(UTC).strftime("%Y-%m-%dT%H%MZ")
summary = {
    "built_at": built_at,
    "corpus": {
        "companies": companies,
        "filings": filings,
        "sections": sections,
        "chunks": indexed,
        "chunks_by_section": dict(by_section),
        "embedded_this_run": embedded,
    },
    "table_bytes": sizes,
    "database_bytes": db_bytes,
    "database_mb": round(db_bytes / 1024 / 1024, 1),
    "under_limit": db_bytes < DB_LIMIT_BYTES,
}
SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
out = SUMMARY_DIR / f"{built_at}.json"
out.write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary, indent=2))
print(out)
