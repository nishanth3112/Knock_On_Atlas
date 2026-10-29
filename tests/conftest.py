import socket
from collections.abc import Iterator
from typing import Any

import pytest

from knock_on_atlas.config import Settings, get_settings

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


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
