"""Score BM25, dense and hybrid retrieval on the labelled queries.

Labels live in evals/retrieval/labels.csv; `relevant` holds `accession|section|quote`
entries separated by `;`, and rows with it empty are skipped. Writes
evals/reports/retrieval_<UTC timestamp>.json. With --suggest, it instead prints the
top hits of each unlabelled query in that format, to copy into the file.

Usage: uv run python scripts/eval_retrieval.py [--suggest]
"""

import argparse
import csv
import json
import statistics
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg

from knock_on_atlas.config import get_settings
from knock_on_atlas.retrieval.embed import BgeEmbedder
from knock_on_atlas.retrieval.hybrid import rankings, rrf
from knock_on_atlas.retrieval.metrics import (
    hit_rate_at,
    judge,
    mrr_at,
    ndcg_at,
    parse_passages,
    recall_at,
)

LABELS = Path("evals/retrieval/labels.csv")
REPORTS = Path("evals/reports")
K = 10

parser = argparse.ArgumentParser()
parser.add_argument("--suggest", action="store_true")
args = parser.parse_args()

rows = list(csv.DictReader(LABELS.open()))
labelled = [r for r in rows if r["relevant"].strip()]
scores: dict[str, list[dict[str, float]]] = {"bm25": [], "dense": [], "hybrid": []}
per_query = []

url = get_settings().database_url.get_secret_value()
with psycopg.connect(url, autocommit=True) as conn:
    embedder = BgeEmbedder()

    def chunks(ids: list[int]) -> list[tuple[str, str, str]]:
        found = {
            row[0]: row[1:]
            for row in conn.execute(
                "SELECT c.id, d.accession, d.section, c.content FROM document_chunks c"
                " JOIN documents d ON d.id = c.document_id WHERE c.id = ANY(%s)",
                (ids,),
            )
        }
        return [found[i] for i in ids]

    for row in rows:
        if row not in labelled and not args.suggest:
            continue
        ciks = row["ciks"].split("|")
        ranked = rankings(
            conn, embedder, row["query"], ciks, date.fromisoformat(row["as_of"])
        )
        ranked["hybrid"] = [cid for cid, _ in rrf(ranked)]
        if row in labelled:
            passages = parse_passages(row["relevant"])
            result: dict[str, dict[str, float]] = {}
            for name, ids in ranked.items():
                relevant = judge(chunks(ids[:K]), passages)
                result[name] = {
                    "recall@10": recall_at(relevant, len(passages), K),
                    "ndcg@10": ndcg_at(relevant, len(passages), K),
                    "mrr@10": mrr_at(relevant, K),
                    "hit_rate@5": hit_rate_at(relevant, 5),
                }
                scores[name].append(result[name])
            per_query.append({"query_id": row["query_id"], **result})
        elif args.suggest:
            print(f"\n{row['query_id']}: {row['query']}")
            for accession, section, content in chunks(ranked["hybrid"][:5]):
                quote = " ".join(content.split()[:12])
                print(f"  {accession}|{section}|{quote}")

if args.suggest:
    raise SystemExit

means = {
    name: {
        metric: round(statistics.mean(q[metric] for q in results), 4)
        for metric in ("recall@10", "ndcg@10", "mrr@10", "hit_rate@5")
    }
    for name, results in scores.items()
    if results
}
built_at = datetime.now(UTC).strftime("%Y-%m-%dT%H%MZ")
report = {
    "built_at": built_at,
    "queries": len(rows),
    "labelled": len(labelled),
    "metrics": means,
    "per_query": per_query,
}
REPORTS.mkdir(parents=True, exist_ok=True)
out = REPORTS / f"retrieval_{built_at}.json"
out.write_text(json.dumps(report, indent=2) + "\n")
print(f"{len(labelled)} of {len(rows)} queries labelled -> {out}")
if means:
    print(json.dumps(means, indent=2))
