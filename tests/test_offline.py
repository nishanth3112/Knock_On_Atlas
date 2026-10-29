import socket

import pytest

from knock_on_atlas.config import JevRoute, LLMProvider, get_settings


def test_outbound_connection_blocked() -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s,
        pytest.raises(RuntimeError, match="tests run offline"),
    ):
        s.connect(("93.184.216.34", 80))


def test_dns_lookup_blocked() -> None:
    with pytest.raises(RuntimeError, match="tests run offline"):
        socket.getaddrinfo("www.sec.gov", 443)


def test_localhost_allowed() -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port)):
            pass


def test_settings_are_offline_and_keyless() -> None:
    s = get_settings()
    assert s.llm_provider is LLMProvider.FAKE
    assert s.jev_route is JevRoute.OFF
    assert s.groq_api_key is None
    assert s.typesafe_api_key is None
