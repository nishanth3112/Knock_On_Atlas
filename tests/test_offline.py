import socket
from pathlib import Path

import pytest

from knock_on_atlas.config import JevRoute, LLMProvider, get_settings


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
@pytest.mark.parametrize(
    ("family", "address"),
    [
        (socket.AF_INET, ("93.184.216.34", 80)),
        (socket.AF_INET6, ("2606:4700:4700::1111", 443, 0, 0)),
    ],
)
def test_outbound_connection_blocked(
    method: str, family: socket.AddressFamily, address: tuple[object, ...]
) -> None:
    with (
        socket.socket(family, socket.SOCK_STREAM) as s,
        pytest.raises(RuntimeError, match="tests run offline"),
    ):
        getattr(s, method)(address)


def test_dns_lookup_blocked() -> None:
    with pytest.raises(RuntimeError, match="tests run offline"):
        socket.getaddrinfo("www.sec.gov", 443)


def test_localhost_allowed() -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port)):
            pass


def test_dotenv_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # CI has no .env, so without this test the .env isolation would be unproven.
    (tmp_path / ".env").write_text("GROQ_API_KEY=gsk_leak\nLOGFIRE_TOKEN=lf_leak\n")
    monkeypatch.chdir(tmp_path)
    s = get_settings()
    assert s.groq_api_key is None
    assert s.logfire_token is None


def test_settings_are_offline_and_keyless() -> None:
    s = get_settings()
    assert s.llm_provider is LLMProvider.FAKE
    assert s.jev_route is JevRoute.OFF
    assert s.groq_api_key is None
    assert s.typesafe_api_key is None
