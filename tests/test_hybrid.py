from datetime import date

import psycopg
import pytest

from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.edgar.filings import Filing
from knock_on_atlas.retrieval import hybrid
from knock_on_atlas.retrieval.bm25 import build_index
from knock_on_atlas.retrieval.corpus import store_section
from knock_on_atlas.retrieval.embed import Embedder, embed_missing
from knock_on_atlas.retrieval.hybrid import RRF_K, hybrid_search, rankings, rrf

BG, JAKKS = "0001278027", "0001009829"


def test_rrf_adds_reciprocal_ranks() -> None:
    fused = dict(rrf({"bm25": [1, 2], "dense": [2, 3]}))
    assert fused[2] == pytest.approx(1 / (RRF_K + 2) + 1 / (RRF_K + 1))
    assert fused[1] == pytest.approx(1 / (RRF_K + 1))
    # Found by both retrievers beats first place in one.
    assert [cid for cid, _ in rrf({"bm25": [1, 2], "dense": [2, 3]})] == [2, 1, 3]


@pytest.fixture
def corpus(db: psycopg.Connection, fake_embedder: Embedder) -> psycopg.Connection:
    migrate(db)
    sections = [
        (BG, "bg-1", date(2026, 3, 3), "Walmart accounted for 31% of our net sales."),
        (BG, "bg-2", date(2026, 3, 3), "Pickles and peppers are made in Iowa."),
        (BG, "bg-3", date(2026, 9, 1), "Walmart cut its orders this quarter."),
        (
            JAKKS,
            "jk-1",
            date(2026, 2, 20),
            "Walmart accounted for 31% of our net sales.",
        ),
    ]
    for cik, accession, filed, text in sections:
        f = Filing(cik, accession, "10-K", filed, None, "doc.htm")
        store_section(db, f, "item_1", text)
    embed_missing(db, fake_embedder)
    build_index(db)
    return db


def test_hybrid_search_is_scoped_dated_and_cited(
    corpus: psycopg.Connection, fake_embedder: Embedder
) -> None:
    hits = hybrid_search(
        corpus, fake_embedder, "Walmart share of net sales", [BG], date(2026, 6, 30)
    )
    # JAKKS has the same sentence but is out of scope; bg-3 was filed after as_of.
    assert [h.accession for h in hits][0] == "bg-1"
    assert {h.accession for h in hits} <= {"bg-1", "bg-2"}
    top = hits[0]
    assert top.ranks == {"bm25": 1, "dense": 1}
    assert (top.cik, top.form, top.section) == (BG, "10-K", "item_1")
    assert top.url.endswith("/doc.htm")
    assert top.source_date == date(2026, 3, 3)


def test_later_filings_appear_once_as_of_allows(
    corpus: psycopg.Connection, fake_embedder: Embedder
) -> None:
    hits = hybrid_search(
        corpus, fake_embedder, "Walmart orders", [BG], date(2026, 12, 31)
    )
    assert "bg-3" in {h.accession for h in hits}


def test_search_refuses_an_unscoped_query(
    corpus: psycopg.Connection, fake_embedder: Embedder
) -> None:
    with pytest.raises(ValueError, match="CIK"):
        rankings(corpus, fake_embedder, "Walmart", [], date(2026, 12, 31))


def test_chunk_removed_after_ranking_is_skipped(
    corpus: psycopg.Connection,
    fake_embedder: Embedder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def rank_then_rechunk(
        conn: psycopg.Connection,
        embedder: Embedder,
        query: str,
        ciks: list[str],
        as_of: date,
    ) -> dict[str, list[int]]:
        ranked = rankings(conn, embedder, query, ciks, as_of)
        conn.execute("DELETE FROM document_chunks WHERE id = %s", (ranked["bm25"][0],))
        return ranked

    monkeypatch.setattr(hybrid, "rankings", rank_then_rechunk)
    hits = hybrid_search(
        corpus, fake_embedder, "Walmart share of net sales", [BG], date(2026, 6, 30)
    )
    assert hits and "bg-1" not in {h.accession for h in hits}
