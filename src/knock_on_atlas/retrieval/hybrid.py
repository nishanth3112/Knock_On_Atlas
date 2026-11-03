from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import psycopg

from knock_on_atlas.retrieval.bm25 import bm25_search
from knock_on_atlas.retrieval.embed import Embedder, to_pgvector

RRF_K = 60
# Each retriever's depth before fusion.
CANDIDATES = 50


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    content: str
    cik: str
    form: str
    section: str
    accession: str
    source_date: date
    url: str
    score: float
    ranks: dict[str, int]  # retriever -> 1-based rank, for the ones that found it


def rrf(rankings: dict[str, list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal rank fusion: each list adds 1 / (k + rank) to every id in it."""
    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings.values():
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] += 1 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def dense_search(
    conn: psycopg.Connection, vector: list[float], ciks: list[str], as_of: date, k: int
) -> list[int]:
    with conn.transaction():
        # Without it, HNSW returns its first candidates and the filter can drop
        # most of them; iterative scans keep reading the index until k remain.
        conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        rows = conn.execute(
            "SELECT id FROM document_chunks"
            " WHERE cik = ANY(%s) AND source_date <= %s AND embedding IS NOT NULL"
            " ORDER BY embedding <=> %s::vector LIMIT %s",
            (ciks, as_of, to_pgvector(vector), k),
        ).fetchall()
    return [row[0] for row in rows]


def rankings(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    ciks: list[str],
    as_of: date,
    depth: int = CANDIDATES,
) -> dict[str, list[int]]:
    """Chunk ids per retriever, best first, for these companies filed by as_of."""
    if not ciks:
        raise ValueError(
            "search needs at least one CIK; unscoped search is not allowed"
        )
    return {
        "bm25": [cid for cid, _ in bm25_search(conn, query, ciks, as_of, depth)],
        "dense": dense_search(conn, embedder.query(query), ciks, as_of, depth),
    }


def hybrid_search(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    ciks: list[str],
    as_of: date,
    k: int = 10,
) -> list[Hit]:
    """The k best chunks by RRF over BM25 and dense, each with its citation."""
    ranked = rankings(conn, embedder, query, ciks, as_of)
    fused = rrf(ranked)[:k]
    rows = {
        row[0]: row
        for row in conn.execute(
            "SELECT c.id, c.content, c.cik, d.form, d.section, d.accession,"
            " c.source_date, d.url"
            " FROM document_chunks c JOIN documents d ON d.id = c.document_id"
            " WHERE c.id = ANY(%s)",
            ([chunk_id for chunk_id, _ in fused],),
        )
    }
    hits = []
    for chunk_id, score in fused:
        _, content, cik, form, section, accession, source_date, url = rows[chunk_id]
        ranks = {
            name: ids.index(chunk_id) + 1
            for name, ids in ranked.items()
            if chunk_id in ids
        }
        hits.append(
            Hit(
                chunk_id=chunk_id,
                content=content,
                cik=cik,
                form=form,
                section=section,
                accession=accession,
                source_date=source_date,
                url=url,
                score=score,
                ranks=ranks,
            )
        )
    return hits
