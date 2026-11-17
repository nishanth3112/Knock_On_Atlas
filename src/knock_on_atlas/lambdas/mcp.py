"""koa-mcp: the edgar-intel MCP server behind a function URL.

Search is BM25 alone here: the embedding model doesn't fit in the bundle.
"""

from functools import cache
from pathlib import Path
from typing import Any

import jwt
import psycopg
from mangum import Mangum

from knock_on_atlas.config import Settings, get_settings
from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.lambdas.secrets import load_secrets
from knock_on_atlas.mcp_server.client import AUDIENCE
from knock_on_atlas.mcp_server.server import build_server
from knock_on_atlas.observability import configure

SEC_CACHE = Path("/tmp/sec_cache")  # the only writable path on Lambda


@cache
def settings() -> Settings:
    load_secrets()
    loaded = get_settings()
    configure(loaded)
    return loaded


def mcp_app(conn: psycopg.Connection, sec: SecClient, secret: str) -> Mangum:
    server = build_server(conn, sec, None, secret)
    # lifespan on: the streamable-HTTP session manager starts in the app's lifespan.
    app = server.http_app(stateless_http=True, json_response=True)
    return Mangum(app, lifespan="on")


def signed(event: dict[str, Any], secret: str) -> bool:
    """A worker's token; checked before Neon is touched, so anonymous traffic to the
    public URL can't wake the database. fastmcp still checks each tool's scope."""
    auth = (event.get("headers") or {}).get("authorization", "")
    try:
        jwt.decode(
            auth.removeprefix("Bearer "),
            secret,
            algorithms=["HS256"],
            audience=AUDIENCE,
        )
    except jwt.InvalidTokenError:
        return False
    return True


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    s = settings()
    if s.mcp_jwt_secret is None:
        raise RuntimeError("koa-mcp needs MCP_JWT_SECRET")
    secret = s.mcp_jwt_secret.get_secret_value()
    if not signed(event, secret):
        return {"statusCode": 401, "body": "unauthorized"}
    # A connection per request: Neon suspends when idle, so a cached one goes stale.
    url = s.database_url.get_secret_value()
    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as conn:
        sec = SecClient(s.sec_user_agent, SEC_CACHE)
        return mcp_app(conn, sec, secret)(event, context)
