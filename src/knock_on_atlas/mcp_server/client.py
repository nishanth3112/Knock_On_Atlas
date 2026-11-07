"""How the worker reaches edgar-intel: over streamable HTTP with a scoped JWT.

Locally the server's ASGI app runs in-process, but through the same HTTP and auth
path as the Lambda, so scopes are checked on every call either way.
"""

import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import jwt
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

TOKEN_TTL_SECONDS = 600


def service_token(
    secret: str,
    scopes: Iterable[str],
    subject: str,
    ttl_seconds: int = TOKEN_TTL_SECONDS,
) -> str:
    now = int(time.time())
    claims = {
        "sub": subject,
        "scope": " ".join(sorted(scopes)),
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


@asynccontextmanager
async def local_client(
    server: FastMCP, token: str | None
) -> AsyncGenerator[Client[StreamableHttpTransport]]:
    app = server.http_app(stateless_http=True, json_response=True)

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        **kwargs: Any,  # fastmcp also passes follow_redirects
    ) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://mcp",
            headers=headers,
            timeout=timeout,
            auth=auth,
            **kwargs,
        )

    transport = StreamableHttpTransport(
        "http://mcp/mcp", auth=token, httpx_client_factory=factory
    )
    async with app.router.lifespan_context(app), Client(transport) as client:
        yield client


def mcp_caller(
    client: Client[StreamableHttpTransport],
) -> Callable[[str, dict[str, Any]], Awaitable[Any]]:
    """Tool calls for agents: the structured result, or the refusal as data."""

    async def call(name: str, args: dict[str, Any]) -> Any:
        try:
            return (await client.call_tool(name, args)).structured_content
        except ToolError as error:
            return {"error": str(error)}

    return call
