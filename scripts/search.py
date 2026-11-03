"""Hybrid search over filing chunks, printed with citations.

Usage: uv run python scripts/search.py "Walmart share of net sales" \
    --cik 0001278027 [--cik ...] [--as-of 2026-06-30] [--k 5]
"""

import argparse
from datetime import date

import psycopg

from knock_on_atlas.config import get_settings
from knock_on_atlas.retrieval.embed import BgeEmbedder
from knock_on_atlas.retrieval.hybrid import hybrid_search

parser = argparse.ArgumentParser()
parser.add_argument("query")
parser.add_argument("--cik", action="append", required=True)
parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
parser.add_argument("--k", type=int, default=5)
args = parser.parse_args()

url = get_settings().database_url.get_secret_value()
with psycopg.connect(url, autocommit=True) as conn:
    hits = hybrid_search(conn, BgeEmbedder(), args.query, args.cik, args.as_of, args.k)
for n, hit in enumerate(hits, start=1):
    ranks = ", ".join(f"{name} #{rank}" for name, rank in hit.ranks.items())
    print(
        f"[{n}] {hit.form} {hit.section} {hit.accession}"
        f" filed {hit.source_date} ({ranks})"
    )
    print(f"    {hit.url}")
    print(f"    {hit.content[:400].replace(chr(10), ' ')}")
