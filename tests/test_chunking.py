from datetime import date

import psycopg
import pytest

from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.edgar.filings import Filing
from knock_on_atlas.retrieval import corpus
from knock_on_atlas.retrieval.chunking import (
    OVERLAP_WORDS,
    WORDS_PER_CHUNK,
    chunk_section,
)
from knock_on_atlas.retrieval.corpus import store_section


def words(prefix: str, n: int) -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


def test_short_section_is_one_chunk_with_its_paragraphs() -> None:
    text = "Item 1. Business.\n" + words("a", 50) + "\n\n" + words("b", 30)
    assert chunk_section(text) == [
        "Item 1. Business.\n" + words("a", 50) + "\n" + words("b", 30)
    ]


def test_chunks_overlap_and_stay_under_the_limit() -> None:
    paragraphs = [words(f"p{n}_", 100) for n in range(10)]
    chunks = chunk_section("\n".join(paragraphs))
    assert len(chunks) > 1
    for before, after in zip(chunks, chunks[1:], strict=False):
        assert after.split()[:OVERLAP_WORDS] == before.split()[-OVERLAP_WORDS:]
    assert max(len(c.split()) for c in chunks) <= WORDS_PER_CHUNK + OVERLAP_WORDS
    # Every word of the section lands in some chunk.
    assert set(" ".join(paragraphs).split()) <= set(" ".join(chunks).split())


def test_long_paragraph_is_split() -> None:
    chunks = chunk_section(words("w", WORDS_PER_CHUNK * 3))
    assert len(chunks) == 3
    assert all(len(c.split()) <= WORDS_PER_CHUNK + OVERLAP_WORDS for c in chunks)


def test_empty_section_has_no_chunks() -> None:
    assert chunk_section("") == []
    assert chunk_section("\n \n") == []


def section_filing() -> Filing:
    return Filing(
        "0001278027",
        "0001104659-26-022961",
        "10-K",
        date(2026, 3, 3),
        date(2026, 1, 3),
        "bgs-20260103x10k.htm",
    )


def test_store_section_chunks_once_and_rechunks_on_change(
    db: psycopg.Connection,
) -> None:
    migrate(db)
    filing = section_filing()
    text = words("a", WORDS_PER_CHUNK * 2)
    assert store_section(db, filing, "item_1", text) == 2
    assert store_section(db, filing, "item_1", text) == 0
    assert store_section(db, filing, "item_1", words("b", 10)) == 1
    rows = db.execute(
        "SELECT ord, cik, source_date, metadata FROM document_chunks"
    ).fetchall()
    assert rows == [
        (
            0,
            "0001278027",
            date(2026, 3, 3),
            {
                "cik": "0001278027",
                "form": "10-K",
                "period": "2026-01-03",
                "section": "item_1",
                "accession": "0001104659-26-022961",
            },
        )
    ]


def test_store_section_is_all_or_nothing(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate(db)
    filing = section_filing()
    store_section(db, filing, "item_1", "old text")

    def crash(text: str) -> list[str]:
        raise RuntimeError("stopped mid-write")

    # A run that stops after the new hash is known must not keep it.
    monkeypatch.setattr(corpus, "chunk_section", crash)
    with pytest.raises(RuntimeError):
        store_section(db, filing, "item_1", "new text")
    monkeypatch.undo()
    assert store_section(db, filing, "item_1", "new text") == 1
    assert db.execute("SELECT content FROM document_chunks").fetchall() == [
        ("new text",)
    ]
