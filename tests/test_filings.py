from datetime import date

import psycopg

from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.edgar.filings import (
    MIN_SECTION_CHARS,
    Filing,
    extract_sections,
    html_to_lines,
    recent_filings,
)
from knock_on_atlas.retrieval.corpus import upsert_document

BODY = "Walmart accounted for 31% of net sales in fiscal 2025. " * 40

# Shaped like the filings checked live (B&G Foods, Walmart, Ford): inline XBRL's
# hidden header, a table of contents in table cells, then the sections, with
# Ford-style headings repeated on each page.
TEN_K = f"""<html><body>
<div style="display:none"><ix:header>
<ix:hidden>HiddenFact 123</ix:hidden>
</ix:header></div>
<script>var x = 1;</script>
<table>
<tr><td>Item 1.</td><td>Business</td><td>3</td></tr>
<tr><td>Item 1A.</td><td>Risk Factors</td><td>12</td></tr>
<tr><td>Item 7.</td><td>Management&#8217;s Discussion</td><td>30</td></tr>
</table>
<div><span>Item&#160;1. Business.</span></div><p>{BODY}</p>
<div>Item 1. Business (Continued)</div><p>Second page of the business section.</p>
<div>ITEM 1A. RISK FACTORS</div><p>{BODY}</p>
<div>Item 1B. Unresolved Staff Comments.</div><p>None.</p>
<div>Item 7. Management&#8217;s Discussion and Analysis</div><p>Not applicable.</p>
<div>Item 8. Financial Statements</div><p>{BODY}</p>
</body></html>""".encode()

TEN_Q = f"""<html><body>
<div>Item 2. Management's Discussion and Analysis</div><p>{BODY}</p>
<div>Item 3. Quantitative and Qualitative Disclosures</div><p>None.</p>
<div>PART II</div>
<div>Item 2. Unregistered Sales of Equity Securities</div><p>None.</p>
</body></html>""".encode()


def test_html_to_lines_keeps_visible_text_only() -> None:
    lines = html_to_lines(TEN_K)
    text = "\n".join(lines)
    assert "HiddenFact" not in text and "var x" not in text
    assert "Item 1. Business." in lines  # &#160; became a plain space
    assert "Item 1. Business 3" in lines  # table cells joined on one line


def test_sections_skip_the_table_of_contents_and_join_repeated_headings() -> None:
    sections = extract_sections(html_to_lines(TEN_K), "10-K")
    assert sorted(sections) == ["item_1", "item_1a"]
    assert all(len(text) >= MIN_SECTION_CHARS for text in sections.values())
    assert sections["item_1"].startswith("Item 1. Business.")
    assert "Second page of the business section." in sections["item_1"]
    # Item 7 is only a TOC line and "Not applicable", so it is not kept.
    assert "Item 1A" not in sections["item_1"]


def test_10q_takes_mdna_not_part_ii_item_2() -> None:
    sections = extract_sections(html_to_lines(TEN_Q), "10-Q")
    assert sections["item_2"].startswith("Item 2. Management's Discussion")
    assert "Unregistered" not in sections["item_2"]


def test_recent_filings_keeps_forms_since_a_date(sec_client: SecClient) -> None:
    filings = recent_filings(sec_client, 1278027, {"10-K", "10-Q"}, date(2025, 1, 1))
    assert [(f.form, f.filed) for f in filings] == [
        ("10-Q", date(2026, 8, 12)),
        ("10-Q", date(2026, 5, 13)),
        ("10-K", date(2026, 3, 3)),
        ("10-K", date(2025, 2, 25)),
    ]
    assert filings[2].period == date(2026, 1, 3)
    assert filings[2].url == (
        "https://www.sec.gov/Archives/edgar/data/1278027/"
        "000110465926022961/bgs-20260103x10k.htm"
    )


def test_upsert_document_reports_only_new_or_changed(db: psycopg.Connection) -> None:
    migrate(db)
    filing = Filing(
        "0001278027",
        "0001104659-26-022961",
        "10-K",
        date(2026, 3, 3),
        date(2026, 1, 3),
        "x.htm",
    )
    first = upsert_document(db, filing, "item_1", "text one")
    assert first is not None
    assert upsert_document(db, filing, "item_1", "text one") is None
    assert upsert_document(db, filing, "item_1", "text two") == first


def test_filing_without_item_headings_has_no_sections() -> None:
    # Cardinal Health's 10-K uses a cross-reference index instead of item headings.
    lines = html_to_lines(f"<div>Business overview</div><p>{BODY}</p>".encode())
    assert extract_sections(lines, "10-K") == {}
