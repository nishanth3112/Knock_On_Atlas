import hashlib

import psycopg

from knock_on_atlas.edgar.filings import Filing


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
