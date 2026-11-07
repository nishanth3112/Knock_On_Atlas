import asyncio
import inspect
import re
from datetime import date
from typing import Any

import psycopg
import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from knock_on_atlas.db.migrate import migrate
from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.edgar.filings import Filing
from knock_on_atlas.graph_store.edges import Edge, replace_edges
from knock_on_atlas.mcp_server import server as server_module
from knock_on_atlas.mcp_server.client import local_client, service_token
from knock_on_atlas.mcp_server.server import TOOL_SCOPES, build_server
from knock_on_atlas.retrieval.bm25 import build_index
from knock_on_atlas.retrieval.corpus import store_section
from knock_on_atlas.retrieval.embed import Embedder, embed_missing

SECRET = "test-secret-at-least-32-bytes-long!!"
FORD, SUPPLIER = "0000037996", "0000012345"
FORD_8K = "0000037996-25-000238"
ALL_SCOPES = set(TOOL_SCOPES.values())


def seeded_server(
    db: psycopg.Connection, sec_client: SecClient, fake_embedder: Embedder
) -> FastMCP:
    """Ford, one approved supplier with a fact and an indexed 10-K line."""
    migrate(db)
    db.execute(
        "INSERT INTO companies (cik, name, sic, tickers, is_hub) VALUES"
        " (%s, 'Ford Motor Co', '3711', '{F}', true),"
        " (%s, 'Gentherm Inc', '3714', '{THRM}', false)",
        (FORD, SUPPLIER),
    )
    replace_edges(
        db,
        [
            Edge(
                src_cik=SUPPLIER,
                dst_cik=FORD,
                pct=22,
                period_end=date(2024, 12, 31),
                method="xbrl",
                extraction_method="alias_exact",
                member_label="Ford Motor Company",
                total_share=True,
                relationship_flag=None,
                source_accession="0000012345-25-000001",
                confidence=1.0,
                status="approved",
                valid_from=date(2025, 2, 1),
                valid_to=None,
            )
        ],
    )
    db.execute(
        "INSERT INTO concentration_facts (cik, accession, form, filed, period_end,"
        " qtrs, member, member_label, pct, benchmark, segments, total_share, is_named)"
        " VALUES (%s, '0000012345-25-000001', '10-K', '2025-02-20', '2024-12-31', 4,"
        " 'FordMember', 'Ford Motor Company', 22, 'Revenues', '', true, true)",
        (SUPPLIER,),
    )
    filing = Filing(
        SUPPLIER, "0000012345-25-000001", "10-K", date(2025, 2, 20), None, "g.htm"
    )
    store_section(
        db, filing, "item_1", "Ford accounted for 22% of our product revenues."
    )
    embed_missing(db, fake_embedder)
    build_index(db)
    return build_server(db, sec_client, fake_embedder, SECRET)


@pytest.fixture
def server(
    db: psycopg.Connection, sec_client: SecClient, fake_embedder: Embedder
) -> FastMCP:
    return seeded_server(db, sec_client, fake_embedder)


def call(server: FastMCP, scopes: set[str], tool: str, args: dict[str, Any]) -> Any:
    async def run() -> Any:
        token = service_token(SECRET, scopes, "test")
        async with local_client(server, token) as client:
            # Raised here, a ToolError would leave wrapped by the server's task group.
            try:
                return (await client.call_tool(tool, args)).structured_content
            except ToolError as error:
                return error

    result = asyncio.run(run())
    if isinstance(result, ToolError):
        raise result
    return result


def listed(server: FastMCP, scopes: set[str]) -> set[str]:
    async def run() -> set[str]:
        async with local_client(server, service_token(SECRET, scopes, "t")) as client:
            return {t.name for t in await client.list_tools()}

    return asyncio.run(run())


def test_tools_read_companies_edges_facts_and_search(server: FastMCP) -> None:
    company = call(server, ALL_SCOPES, "get_company", {"cik": "37996"})
    assert company["name"] == "Ford Motor Co" and company["is_hub"]

    edges = call(
        server,
        ALL_SCOPES,
        "get_exposure_edges",
        {"hub_cik": FORD, "as_of": "2026-01-02"},
    )["result"]
    assert [(e["supplier_cik"], e["weight"], e["depth"]) for e in edges] == [
        (SUPPLIER, 22.0, 1)
    ]

    facts = call(server, ALL_SCOPES, "get_concentration_facts", {"cik": SUPPLIER})
    assert facts["result"][0]["member_label"] == "Ford Motor Company"

    hits = call(
        server,
        ALL_SCOPES,
        "search_filings",
        {"query": "Ford share of revenues", "ciks": [SUPPLIER], "as_of": "2026-01-02"},
    )["result"]
    assert hits[0]["cik"] == SUPPLIER and "22%" in hits[0]["content"]

    page = call(
        server,
        ALL_SCOPES,
        "get_filing_section",
        {"accession": "0000012345-25-000001", "section": "item_1"},
    )
    assert page["chunk_id"] == hits[0]["chunk_id"] and page["pages"] == 1


def test_8k_items_read_the_filing_and_its_exhibit(server: FastMCP) -> None:
    eight_k = call(
        server, ALL_SCOPES, "get_8k_items", {"cik": FORD, "accession": FORD_8K}
    )
    assert eight_k["items"] == ["2.06", "7.01", "9.01"]
    assert eight_k["accepted_at"].startswith("2025-12-15T21:07:14")
    assert eight_k["excerpt"].startswith("Item 2.06. Material Impairments.")
    assert len(eight_k["excerpt"]) <= 1500
    assert eight_k["exhibit_url"].endswith("/exhibit99todecember15202.htm")
    assert "Takes EV-Related Charges" in eight_k["exhibit_excerpt"]


def test_search_without_ciks_is_refused(server: FastMCP) -> None:
    with pytest.raises(ToolError, match="at least one CIK"):
        call(
            server,
            ALL_SCOPES,
            "search_filings",
            {"query": "Ford", "ciks": [], "as_of": "2026-01-02"},
        )


def test_a_tool_outside_the_token_scopes_is_hidden_and_refused(server: FastMCP) -> None:
    assert listed(server, {"read:edgar"}) == {
        name for name, scope in TOOL_SCOPES.items() if scope == "read:edgar"
    }
    with pytest.raises(ToolError):
        call(
            server,
            {"read:edgar"},
            "get_exposure_edges",
            {"hub_cik": FORD, "as_of": "2026-01-02"},
        )


def test_a_call_without_a_valid_token_is_rejected(server: FastMCP) -> None:
    async def run(token: str | None) -> None:
        async with local_client(server, token) as client:
            await client.list_tools()

    with pytest.raises(Exception):  # noqa: B017 - the transport raises on HTTP 401
        asyncio.run(run(None))
    forged = service_token("another-secret-that-is-32-bytes-long", ALL_SCOPES, "x")
    with pytest.raises(Exception):  # noqa: B017
        asyncio.run(run(forged))
    expired = service_token(SECRET, ALL_SCOPES, "x", ttl_seconds=-1)
    with pytest.raises(Exception):  # noqa: B017
        asyncio.run(run(expired))


def test_every_tool_is_a_known_read_only_tool(server: FastMCP) -> None:
    assert listed(server, ALL_SCOPES) == set(TOOL_SCOPES)
    # The server issues no SQL that changes data.
    source = inspect.getsource(server_module)
    assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", source)
