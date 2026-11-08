"""edgar-intel: the read-only tools agents reach SEC data, edges and search through.

Every tool needs a JWT scope. There is no tool that writes anything, so text an
agent reads can never create an edge.
"""

import re
from datetime import date, datetime

import psycopg
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import require_scopes
from fastmcp.server.auth.providers.jwt import JWTVerifier
from pydantic import BaseModel

from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.edgar.filings import html_to_lines, recent_filings
from knock_on_atlas.edgar.submissions import recent_8ks
from knock_on_atlas.edgar.tickers import pad_cik
from knock_on_atlas.graph_store.traversal import Exposure, exposure
from knock_on_atlas.mcp_server.client import AUDIENCE
from knock_on_atlas.retrieval.embed import Embedder
from knock_on_atlas.retrieval.hybrid import Hit, hybrid_search

TOOL_SCOPES = {
    "get_company": "read:edgar",
    "list_filings": "read:edgar",
    "get_8k_items": "read:edgar",
    "get_filing_section": "read:edgar",
    "get_concentration_facts": "read:facts",
    "get_exposure_edges": "read:graph",
    "search_filings": "read:edgar",
}
# J1 reads the headline and opening of the 8-K text (spec §12).
EXCERPT_CHARS = 1500
MAX_SEARCH_HITS = 20
_ITEM_LINE = re.compile(r"(?i)^item\s+\d+\.\d+")
# Filing indexes carry file names only; exhibits 99.x are named like this.
_EX99 = re.compile(r"(?i)ex(hibit)?[-_]?99.*\.htm")


class Company(BaseModel):
    cik: str
    name: str
    sic: str | None
    tickers: list[str]
    is_hub: bool


class FilingRef(BaseModel):
    accession: str
    form: str
    filed: date
    url: str


class EightK(BaseModel):
    cik: str
    accession: str
    accepted_at: datetime
    items: list[str]
    url: str
    excerpt: str
    exhibit_url: str | None
    exhibit_excerpt: str | None


class Section(BaseModel):
    """One stored chunk of a filing section; a chunk id is what a brief cites."""

    chunk_id: int
    accession: str
    section: str
    page: int
    pages: int
    text: str
    url: str


class ConcentrationFact(BaseModel):
    member_label: str
    pct: float | None
    period_end: date
    accession: str
    total_share: bool
    is_named: bool


def build_server(
    conn: psycopg.Connection, sec: SecClient, embedder: Embedder, jwt_secret: str
) -> FastMCP:
    mcp = FastMCP(
        "edgar-intel",
        auth=JWTVerifier(public_key=jwt_secret, algorithm="HS256", audience=AUDIENCE),
    )

    def get_company(cik: str) -> Company:
        """Name, SIC, tickers and hub flag for one company."""
        row = conn.execute(
            "SELECT cik, name, sic, tickers, is_hub FROM companies WHERE cik = %s",
            (pad_cik(cik),),
        ).fetchone()
        if row is None:
            raise ToolError(f"unknown company {cik}")
        return Company(
            cik=row[0], name=row[1], sic=row[2], tickers=row[3], is_hub=row[4]
        )

    def list_filings(cik: str, forms: list[str], since: date) -> list[FilingRef]:
        """A company's filings of the given forms filed on or after `since`."""
        return [
            FilingRef(accession=f.accession, form=f.form, filed=f.filed, url=f.url)
            for f in recent_filings(sec, cik, set(forms), since)
        ]

    def get_8k_items(cik: str, accession: str) -> EightK:
        """Items, acceptance time and the opening text of one 8-K and its EX-99."""
        filing = next(
            (f for f in recent_8ks(sec, cik) if f.accession == accession), None
        )
        if filing is None:
            raise ToolError(f"no 8-K {accession} for {cik}")
        lines = html_to_lines(sec.get(filing.url))
        start = next((i for i, line in enumerate(lines) if _ITEM_LINE.match(line)), 0)
        folder = filing.url.rsplit("/", 1)[0]
        names = [
            f["name"] for f in sec.get_json(f"{folder}/index.json")["directory"]["item"]
        ]
        exhibit = next((n for n in names if _EX99.fullmatch(n)), None)
        exhibit_url = f"{folder}/{exhibit}" if exhibit else None
        return EightK(
            cik=filing.cik,
            accession=accession,
            accepted_at=filing.accepted_at,
            items=list(filing.items),
            url=filing.url,
            excerpt="\n".join(lines[start:])[:EXCERPT_CHARS],
            exhibit_url=exhibit_url,
            exhibit_excerpt=(
                "\n".join(html_to_lines(sec.get(exhibit_url)))[:EXCERPT_CHARS]
                if exhibit_url
                else None
            ),
        )

    def get_filing_section(
        accession: str, section: str, ciks: list[str], page: int = 0
    ) -> Section:
        """One page (a stored chunk) of an indexed 10-K or 10-Q section of `ciks`."""
        if not ciks:
            raise ToolError("reading a section needs at least one CIK")
        rows = conn.execute(
            "SELECT c.id, c.content, d.url FROM document_chunks c"
            " JOIN documents d ON d.id = c.document_id"
            " WHERE d.accession = %s AND d.section = %s AND d.cik = ANY(%s)"
            " ORDER BY c.ord",
            (accession, section, ciks),
        ).fetchall()
        if not 0 <= page < len(rows):
            raise ToolError(f"{accession} {section} has {len(rows)} pages")
        chunk_id, text, url = rows[page]
        return Section(
            chunk_id=chunk_id,
            accession=accession,
            section=section,
            page=page,
            pages=len(rows),
            text=text,
            url=url,
        )

    def get_concentration_facts(cik: str) -> list[ConcentrationFact]:
        """A supplier's customer revenue-concentration facts, newest period first."""
        rows = conn.execute(
            "SELECT member_label, pct, period_end, accession, total_share, is_named"
            " FROM concentration_facts WHERE cik = %s ORDER BY period_end DESC, id",
            (pad_cik(cik),),
        ).fetchall()
        return [
            ConcentrationFact(
                member_label=r[0],
                pct=None if r[1] is None else float(r[1]),
                period_end=r[2],
                accession=r[3],
                total_share=r[4],
                is_named=r[5],
            )
            for r in rows
        ]

    def get_exposure_edges(hub_cik: str, as_of: date) -> list[Exposure]:
        """Suppliers exposed to a hub within 2 hops, on edges valid on as_of."""
        return exposure(conn, pad_cik(hub_cik), as_of)

    def search_filings(
        query: str, ciks: list[str], as_of: date, k: int = 10
    ) -> list[Hit]:
        """Hybrid search over these companies' 10-K/10-Q sections filed by as_of."""
        if not ciks:
            raise ToolError("search needs at least one CIK")
        return hybrid_search(
            conn, embedder, query, ciks, as_of, min(k, MAX_SEARCH_HITS)
        )

    tools = (
        get_company,
        list_filings,
        get_8k_items,
        get_filing_section,
        get_concentration_facts,
        get_exposure_edges,
        search_filings,
    )
    for tool in tools:
        mcp.tool(tool, auth=require_scopes(TOOL_SCOPES[tool.__name__]))
    return mcp
