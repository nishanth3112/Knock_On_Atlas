from datetime import date
from pathlib import Path

import httpx
import pytest

from knock_on_atlas import ratelimit
from knock_on_atlas.market.prices import TwelveDataClient, TwelveDataError

FIXTURES = Path(__file__).parent / "fixtures" / "twelvedata"
KEY = "td_test_key"
JSON = {"content-type": "application/json; charset=utf-8"}


def make_client() -> tuple[TwelveDataClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.params["symbol"] == "SPY":
            body = (FIXTURES / "time_series_SPY.json").read_bytes()
            return httpx.Response(200, content=body, headers=JSON)
        if request.url.params["symbol"] == "PROXY":
            return httpx.Response(502, html="<html>Bad Gateway</html>")
        body = (FIXTURES / "error_404.json").read_bytes()
        return httpx.Response(404, content=body, headers=JSON)

    return TwelveDataClient(KEY, transport=httpx.MockTransport(respond)), seen


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(ratelimit.time, "sleep", recorded.append)
    return recorded


def test_daily_closes_oldest_first() -> None:
    client, seen = make_client()
    closes = client.daily_closes("SPY", date(2026, 10, 28), date(2026, 10, 30))
    assert list(closes.items()) == [
        (date(2026, 10, 28), 99.75),
        (date(2026, 10, 29), 100.25),
        (date(2026, 10, 30), 101.5),
    ]
    # httpx logs request URLs at INFO, so the key must travel in a header.
    assert "apikey" not in str(seen[0].url)
    assert seen[0].headers["Authorization"] == f"apikey {KEY}"
    params = seen[0].url.params
    assert params["interval"] == "1day"
    assert params["start_date"] == "2026-10-28"
    assert params["end_date"] == "2026-10-30"


def test_error_raises_without_leaking_key() -> None:
    client, _ = make_client()
    with pytest.raises(TwelveDataError, match="ZZZZ") as err:
        client.daily_closes("ZZZZ", date(2026, 10, 28), date(2026, 10, 30))
    assert KEY not in str(err.value)


def test_non_json_error_raises_twelve_data_error() -> None:
    client, _ = make_client()
    with pytest.raises(TwelveDataError, match="PROXY: HTTP 502"):
        client.daily_closes("PROXY", date(2026, 10, 28), date(2026, 10, 30))


def test_requests_are_spaced_for_basic_plan(sleeps: list[float]) -> None:
    client, _ = make_client()
    for _ in range(2):
        client.daily_closes("SPY", date(2026, 10, 28), date(2026, 10, 30))
    assert sleeps == pytest.approx([7.5])


def test_share_class_tickers_use_the_dotted_symbol() -> None:
    client, seen = make_client()
    with pytest.raises(TwelveDataError, match="MOG-A"):
        client.daily_closes("MOG-A", date(2026, 10, 28), date(2026, 10, 30))
    # SEC writes MOG-A; Twelve Data knows the same class as MOG.A.
    assert seen[0].url.params["symbol"] == "MOG.A"
