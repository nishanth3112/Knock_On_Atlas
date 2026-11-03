import hashlib

import psycopg
from psycopg.types.json import Jsonb

from knock_on_atlas.edgar.filings import Filing
from knock_on_atlas.retrieval.chunking import chunk_section


def upsert_document(
    conn: psycopg.Connection, filing: Filing, section: str, text: str
) -> int | None:
    """Record one section; returns its id when new or changed, None when unchanged."""
    row = conn.execute(
        "INSERT INTO documents"
        " (cik, accession, form, section, url, published_at, content_hash, chars)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (accession, section) DO UPDATE"
        " SET content_hash = excluded.content_hash, chars = excluded.chars"
        " WHERE documents.content_hash <> excluded.content_hash"
        " RETURNING id",
        (
            filing.cik,
            filing.accession,
            filing.form,
            section,
            filing.url,
            filing.filed,
            hashlib.sha256(text.encode()).hexdigest(),
            len(text),
        ),
    ).fetchone()
    return row[0] if row else None


def store_section(
    conn: psycopg.Connection, filing: Filing, section: str, text: str
) -> int:
    """Record a section and chunk it when new, changed or never chunked.

    Returns the number of chunks written (0 when the stored chunks are current).
    """
    doc_id = upsert_document(conn, filing, section, text)
    if doc_id is None:
        # Unchanged, but a crash after the upsert could have left it without chunks.
        row = conn.execute(
            "SELECT id FROM documents d WHERE accession = %s AND section = %s"
            " AND NOT EXISTS"
            " (SELECT 1 FROM document_chunks c WHERE c.document_id = d.id)",
            (filing.accession, section),
        ).fetchone()
        if row is None:
            return 0
        doc_id = row[0]
    metadata = {
        "cik": filing.cik,
        "form": filing.form,
        "period": filing.period.isoformat() if filing.period else None,
        "section": section,
        "accession": filing.accession,
    }
    chunks = chunk_section(text)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM document_chunks WHERE document_id = %s", (doc_id,))
        cur.executemany(
            "INSERT INTO document_chunks"
            " (document_id, ord, cik, source_date, content, metadata)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (doc_id, ord, filing.cik, filing.filed, chunk, Jsonb(metadata))
                for ord, chunk in enumerate(chunks)
            ],
        )
    return len(chunks)
