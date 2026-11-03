from datetime import date

import psycopg
import pytest

from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.edgar.filings import Filing
from knock_on_atlas.retrieval.bm25 import bm25_search, build_index, tokenize
from knock_on_atlas.retrieval.corpus import store_section

BG, JAKKS = "0001278027", "0001009829"


def test_tokenize_keeps_numbers_percentages_and_names_whole() -> None:
    assert tokenize("Item 8.01: Walmart was 31% of sales; AT&T's U.S. unit, $1,234.5")[
        :6
    ] == ["item", "8.01", "walmart", "was", "31%", "of"]
    assert "at&t's" in tokenize("AT&T's network")
    assert "1,234.5" in tokenize("$1,234.5 million")


def filing(cik: str, accession: str, filed: date) -> Filing:
    return Filing(cik, accession, "10-K", filed, None, "x.htm")


@pytest.fixture
def corpus(db: psycopg.Connection) -> psycopg.Connection:
    migrate(db)
    sections = [
        (
            filing(BG, "a-1", date(2026, 3, 3)),
            "Walmart accounted for 31% of net sales.",
        ),
        (
            filing(BG, "a-2", date(2026, 3, 3)),
            "Our pickles are sold in grocery stores.",
        ),
        (filing(JAKKS, "b-1", date(2026, 2, 20)), "Walmart Walmart Walmart toys."),
        (filing(BG, "a-3", date(2026, 9, 1)), "Walmart reduced orders in 2026."),
    ]
    for f, text in sections:
        store_section(db, f, "item_1", text)
    build_index(db)
    return db


def accessions(db: psycopg.Connection, hits: list[tuple[int, float]]) -> list[str]:
    ids = [chunk_id for chunk_id, _ in hits]
    rows = dict(
        db.execute(
            "SELECT c.id, d.accession FROM document_chunks c"
            " JOIN documents d ON d.id = c.document_id WHERE c.id = ANY(%s)",
            (ids,),
        ).fetchall()
    )
    return [rows[i] for i in ids]


def test_search_ranks_within_scope_and_date(corpus: psycopg.Connection) -> None:
    hits = bm25_search(corpus, "Walmart share of net sales", [BG], date(2026, 6, 30), 5)
    # JAKKS scores higher on "Walmart" but is out of scope; a-3 was filed later.
    assert accessions(corpus, hits) == ["a-1"]
    later = bm25_search(corpus, "Walmart", [BG], date(2026, 12, 31), 5)
    assert sorted(accessions(corpus, later)) == ["a-1", "a-3"]


def test_search_reloads_after_a_rebuild(corpus: psycopg.Connection) -> None:
    assert bm25_search(corpus, "dividend", [BG], date(2026, 12, 31), 5) == []
    store_section(
        corpus, filing(BG, "a-4", date(2026, 3, 3)), "item_1", "A dividend increase."
    )
    build_index(corpus)
    hits = bm25_search(corpus, "dividend", [BG], date(2026, 12, 31), 5)
    assert accessions(corpus, hits) == ["a-4"]


def test_search_without_an_index_says_so(db: psycopg.Connection) -> None:
    migrate(db)
    with pytest.raises(LookupError, match="build_index"):
        bm25_search(db, "Walmart", [BG], date(2026, 12, 31), 5)
