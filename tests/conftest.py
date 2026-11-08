import hashlib
import socket
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from knock_on_atlas.config import Settings, get_settings
from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.retrieval.bm25 import tokenize
from knock_on_atlas.retrieval.embed import DIM
from knock_on_atlas.workflow.checkpoints import dynamodb_saver

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
# docker-compose and CI both publish DynamoDB Local here.
DYNAMODB_LOCAL = "http://127.0.0.1:8002"
SEC_FIXTURES = Path(__file__).parent / "fixtures" / "sec"


class NetworkBlockedError(RuntimeError):
    pass


def _check_host(host: Any) -> None:
    if host not in LOCAL_HOSTS:
        raise NetworkBlockedError(
            f"tests run offline; blocked network access to {host!r}"
        )


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def connect(self: socket.socket, address: Any) -> None:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _check_host(address[0])
        real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _check_host(address[0])
        return real_connect_ex(self, address)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        _check_host(host)
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


@pytest.fixture(autouse=True)
def test_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Keys from the developer's shell or .env must never reach a test.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("JEV_ROUTE", "off")
    monkeypatch.setenv("SEC_USER_AGENT", "Test User test@example.com")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def fixture_sec_client(cache_dir: Path) -> SecClient:
    """Serves tests/fixtures/sec/<last URL segment>, recorded from real SEC data."""

    def respond(request: httpx.Request) -> httpx.Response:
        path = SEC_FIXTURES / request.url.path.rsplit("/", 1)[-1]
        if not path.exists():
            return httpx.Response(404)
        return httpx.Response(200, content=path.read_bytes())

    return SecClient(
        "Test User test@example.com",
        cache_dir,
        transport=httpx.MockTransport(respond),
    )


@pytest.fixture
def sec_client(tmp_path: Path) -> SecClient:
    return fixture_sec_client(tmp_path / "sec_cache")


@pytest.fixture
def db() -> Iterator[psycopg.Connection]:
    """A connection to the local test Postgres, inside a schema dropped afterwards."""
    url = get_settings().database_url.get_secret_value()
    # libpq opens its own sockets, so the guard above never sees them.
    _check_host(conninfo_to_dict(url).get("host"))
    schema = f"test_{uuid.uuid4().hex}"
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
        # public holds the vector extension, shared by every schema.
        conn.execute(f"SET search_path TO {schema}, public")
        try:
            yield conn
        finally:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def checkpoint_table() -> Iterator[str]:
    """A fresh checkpoint table in DynamoDB Local, deleted afterwards."""
    table = f"test-{uuid.uuid4().hex}"
    saver = dynamodb_saver(DYNAMODB_LOCAL, table)  # creates the table
    try:
        yield table
    finally:
        saver.client.delete_table(TableName=table)


class FakeEmbedder:
    """Hashed bag of words: texts sharing words get similar vectors, offline."""

    def passages(self, texts: list[str]) -> list[list[float]]:
        return [self.query(text) for text in texts]

    def query(self, text: str) -> list[float]:
        vector = [0.0] * DIM
        for token in tokenize(text):
            vector[int(hashlib.md5(token.encode()).hexdigest(), 16) % DIM] += 1.0
        norm = sum(x * x for x in vector) ** 0.5 or 1.0
        return [x / norm for x in vector]


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()
