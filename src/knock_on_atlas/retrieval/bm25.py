import io
import json
import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import bm25s
import numpy as np
import psycopg

# Numbers keep their decimals and a trailing %, so "8.01" and "31%" stay whole;
# "&", "'" and "." inside a word keep "at&t", "company's" and "u.s" together.
_TOKEN = re.compile(r"\d+(?:[.,]\d+)*%?|[a-z0-9]+(?:[&'.][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass(frozen=True)
class _Loaded:
    built_at: datetime
    model: bm25s.BM25
    chunk_ids: np.ndarray


# One index per process, reloaded when a rebuild replaces it.
_loaded: _Loaded | None = None


def build_index(conn: psycopg.Connection) -> int:
    """Index every chunk and store the index in Postgres; returns chunks indexed."""
    rows = conn.execute(
        "SELECT id, content FROM document_chunks ORDER BY id"
    ).fetchall()
    model = bm25s.BM25()
    model.index([tokenize(content) for _, content in rows], show_progress=False)
    archive = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(archive, "w") as zf:
        model.save(tmp, show_progress=False)
        for path in Path(tmp).iterdir():
            zf.write(path, path.name)
        zf.writestr("chunk_ids.json", json.dumps([chunk_id for chunk_id, _ in rows]))
    conn.execute(
        "INSERT INTO bm25_index (id, built_at, chunks, archive)"
        " VALUES (1, now(), %s, %s)"
        " ON CONFLICT (id) DO UPDATE SET built_at = excluded.built_at,"
        " chunks = excluded.chunks, archive = excluded.archive",
        (len(rows), archive.getvalue()),
    )
    return len(rows)


def _archive(
    conn: psycopg.Connection, column: Literal["chunks", "archive"]
) -> tuple[datetime, bytes]:
    row = conn.execute(f"SELECT built_at, {column} FROM bm25_index").fetchone()
    if row is None:
        raise LookupError("no BM25 index; run scripts/build_index.py")
    return row[0], row[1]


def _index(conn: psycopg.Connection) -> _Loaded:
    global _loaded
    built_at, _ = _archive(conn, "chunks")
    if _loaded is None or _loaded.built_at != built_at:
        # The archive (tens of MB) is read only when a rebuild replaced it.
        built_at, archive = _archive(conn, "archive")
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(io.BytesIO(archive)) as zf:
                zf.extractall(tmp)
            model = bm25s.BM25.load(tmp, show_progress=False)
            ids = json.loads(Path(tmp, "chunk_ids.json").read_text())
        _loaded = _Loaded(built_at, model, np.array(ids))
    return _loaded


def bm25_search(
    conn: psycopg.Connection, query: str, ciks: list[str], as_of: date, k: int
) -> list[tuple[int, float]]:
    """(chunk id, score) for the k best chunks of these companies filed by as_of."""
    tokens = tokenize(query)
    if not tokens:  # bm25s raises IndexError on an empty query
        return []
    index = _index(conn)
    allowed = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM document_chunks WHERE cik = ANY(%s) AND source_date <= %s",
            (ciks, as_of),
        )
    ]
    mask = np.isin(index.chunk_ids, allowed).astype(np.float32)
    scores = index.model.get_scores(tokens, weight_mask=mask)
    best = [i for i in np.argsort(-scores)[:k] if scores[i] > 0]
    return [(int(index.chunk_ids[i]), float(scores[i])) for i in best]
