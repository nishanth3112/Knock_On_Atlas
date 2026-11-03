from collections.abc import Sequence
from typing import Protocol

import psycopg
from fastembed import TextEmbedding

MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384
MODEL_CACHE = "data/models"
EMBED_BATCH = 256
# fastembed pads each batch to its longest text, so small batches of mixed-length
# chunks ran fastest on CPU (batch sizes measured 2026-11-03).
PASSAGE_BATCH = 8


class Embedder(Protocol):
    def passages(self, texts: list[str]) -> list[list[float]]: ...

    def query(self, text: str) -> list[float]: ...


class BgeEmbedder:
    def __init__(self, cache_dir: str = MODEL_CACHE) -> None:
        self._model = TextEmbedding(MODEL, cache_dir=cache_dir)

    def passages(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.passage_embed(texts, batch_size=PASSAGE_BATCH)
        return [v.tolist() for v in vectors]

    def query(self, text: str) -> list[float]:
        return [float(x) for x in next(iter(self._model.query_embed(text)))]


def to_pgvector(vector: Sequence[float]) -> str:
    """pgvector's text form, so no adapter package is needed."""
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


def embed_missing(conn: psycopg.Connection, embedder: Embedder) -> int:
    """Embed every chunk without a vector, in id order; returns chunks embedded."""
    done = 0
    last_id = 0
    while True:
        rows = conn.execute(
            "SELECT id, content FROM document_chunks"
            " WHERE embedding IS NULL AND id > %s ORDER BY id LIMIT %s",
            (last_id, EMBED_BATCH),
        ).fetchall()
        if not rows:
            return done
        vectors = embedder.passages([content for _, content in rows])
        with conn.transaction(), conn.cursor() as cur:
            cur.executemany(
                "UPDATE document_chunks SET embedding = %s::vector WHERE id = %s",
                [
                    (to_pgvector(v), chunk_id)
                    for (chunk_id, _), v in zip(rows, vectors, strict=True)
                ],
            )
        done += len(rows)
        last_id = rows[-1][0]
