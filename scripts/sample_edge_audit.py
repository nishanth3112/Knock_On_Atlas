"""Write a stratified sample of approved edges for a precision audit.

Label each row in the `label` column with one of the printed labels.
Edge precision = correct / rows.

Writes evals/edge_audit/<UTC timestamp>.csv.

Usage: uv run python scripts/sample_edge_audit.py [--n 100] [--seed 0]
"""

import argparse
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from knock_on_atlas.config import get_settings
from knock_on_atlas.graph_store.review import (
    AUDIT_LABELS,
    audit_sample,
    list_edges,
    stratum,
    write_audit_csv,
)

parser = argparse.ArgumentParser()
parser.add_argument("--n", type=int, default=100)
parser.add_argument("--seed", type=int, default=0)
args = parser.parse_args()

url = get_settings().database_url.get_secret_value()
with psycopg.connect(url) as conn:
    approved = list_edges(conn, "approved")
sample = audit_sample(approved, args.n, args.seed)
# Timestamped, so a later sample never overwrites one being labelled.
built_at = datetime.now(UTC).strftime("%Y-%m-%dT%H%MZ")
out = Path("evals/edge_audit") / f"{built_at}.csv"
write_audit_csv(out, sample)
print(f"{len(sample)} of {len(approved)} approved edges -> {out}")
print(dict(Counter(stratum(e) for e in sample)))
print(f"labels: {', '.join(AUDIT_LABELS)}")
