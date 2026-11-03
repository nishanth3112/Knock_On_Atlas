from datetime import date

import psycopg

from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.edgar.filings import Filing
from knock_on_atlas.retrieval.corpus import store_section
from knock_on_atlas.retrieval.embed import DIM, Embedder, embed_missing, to_pgvector


def test_to_pgvector_is_pgvector_text() -> None:
    assert to_pgvector([0.5, -1.0, 1e-8]) == "[0.5,-1,1e-08]"


def test_embed_missing_fills_every_chunk_once(
    db: psycopg.Connection, fake_embedder: Embedder
) -> None:
    migrate(db)
    for n, text in enumerate(["Walmart net sales", "pickles and peppers", "toy sales"]):
        f = Filing("0001278027", f"a-{n}", "10-K", date(2026, 3, 3), None, "x.htm")
        store_section(db, f, "item_1", text)
    assert embed_missing(db, fake_embedder) == 3
    assert embed_missing(db, fake_embedder) == 0
    dims = db.execute("SELECT vector_dims(embedding) FROM document_chunks").fetchall()
    assert dims == [(DIM,)] * 3
    nearest = db.execute(
        "SELECT content FROM document_chunks ORDER BY embedding <=> %s::vector LIMIT 1",
        (to_pgvector(fake_embedder.query("Walmart sales")),),
    ).fetchone()
    assert nearest == ("Walmart net sales",)
