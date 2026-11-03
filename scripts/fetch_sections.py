"""Fetch 10-K and 10-Q filings for every graph company and record their sections.

Graph companies are the hubs and every company on a non-rejected edge.

Usage: uv run python scripts/fetch_sections.py
"""

import logging
from collections import Counter
from datetime import date
from pathlib import Path

import psycopg

from knock_on_atlas.config import get_settings
from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.edgar.filings import (
    SECTIONS,
    extract_sections,
    html_to_lines,
    recent_filings,
)
from knock_on_atlas.retrieval.corpus import upsert_document

# The first FS&N month the edges come from.
SINCE = date(2025, 11, 1)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("fetch_sections")
settings = get_settings()
sec = SecClient(settings.sec_user_agent, Path("data/raw/sec_cache"))
found: Counter[str] = Counter()
missing: Counter[str] = Counter()
failed: list[str] = []

with psycopg.connect(settings.database_url.get_secret_value(), autocommit=True) as conn:
    ciks = [
        row[0]
        for row in conn.execute(
            "SELECT cik FROM companies WHERE is_hub UNION"
            " SELECT src_cik FROM entity_edges WHERE status <> 'rejected' UNION"
            " SELECT dst_cik FROM entity_edges WHERE status <> 'rejected'"
            " ORDER BY 1"
        )
    ]
    filings = [
        f for cik in ciks for f in recent_filings(sec, cik, set(SECTIONS), SINCE)
    ]
    for filing in filings:
        try:
            sections = extract_sections(html_to_lines(sec.get(filing.url)), filing.form)
        except Exception:  # one bad filing must not stop the rest
            logger.warning("failed %s %s", filing.accession, filing.url, exc_info=True)
            failed.append(filing.accession)
            continue
        for section in SECTIONS[filing.form].values():
            if section in sections:
                upsert_document(conn, filing, section, sections[section])
                found[f"{filing.form}/{section}"] += 1
            else:
                missing[f"{filing.form}/{section}"] += 1

print(f"{len(ciks)} companies, {len(filings)} filings, {len(failed)} failed")
print(f"sections found: {dict(found)}")
print(f"sections missing: {dict(missing)}")
