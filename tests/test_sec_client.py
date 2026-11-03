import os
import time
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from knock_on_atlas import ratelimit
from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.ratelimit import RateLimiter

UA = "Test User test@example.com"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(ratelimit.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(ratelimit.time, "sleep", fake.sleep)
    return fake


def test_rate_limiter_spaces_calls(clock: FakeClock) -> None:
    limiter = RateLimiter(per_second=5)
    for _ in range(3):
        limiter.wait()
    assert clock.sleeps == pytest.approx([0.2, 0.2])


def test_rate_limiter_no_sleep_after_idle(clock: FakeClock) -> None:
    limiter = RateLimiter(per_second=5)
    limiter.wait()
    clock.now += 1.0
    limiter.wait()
    assert clock.sleeps == []


def make_client(tmp_path: Path) -> tuple[SecClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("missing.json"):
            return httpx.Response(404)
        return httpx.Response(200, json={"path": request.url.path})

    client = SecClient(UA, tmp_path / "cache", transport=httpx.MockTransport(respond))
    return client, seen


def test_sends_user_agent(tmp_path: Path, clock: FakeClock) -> None:
    client, seen = make_client(tmp_path)
    assert client.get_json("https://data.sec.gov/a.json") == {"path": "/a.json"}
    assert seen[0].headers["User-Agent"] == UA


def test_cache_hit_skips_network_and_limiter(tmp_path: Path, clock: FakeClock) -> None:
    client, seen = make_client(tmp_path)
    client.get("https://data.sec.gov/a.json")
    client.get("https://data.sec.gov/a.json")
    assert len(seen) == 1
    assert clock.sleeps == []


def test_network_calls_are_rate_limited(tmp_path: Path, clock: FakeClock) -> None:
    client, seen = make_client(tmp_path)
    client.get("https://data.sec.gov/a.json")
    client.get("https://data.sec.gov/b.json")
    assert len(seen) == 2
    assert clock.sleeps == pytest.approx([0.2])


def test_error_raises_and_is_not_cached(tmp_path: Path, clock: FakeClock) -> None:
    client, seen = make_client(tmp_path)
    for _ in range(2):
        with pytest.raises(httpx.HTTPStatusError):
            client.get("https://data.sec.gov/missing.json")
    assert len(seen) == 2


def test_max_age_refetches_only_stale_copies(tmp_path: Path, clock: FakeClock) -> None:
    client, seen = make_client(tmp_path)
    url = "https://data.sec.gov/submissions/CIK0000000001.json"
    client.get(url)
    client.get(url, max_age=timedelta(days=1))
    assert len(seen) == 1
    (cached,) = (tmp_path / "cache").iterdir()
    two_days_ago = time.time() - 2 * 86400
    os.utime(cached, (two_days_ago, two_days_ago))
    client.get(url, max_age=timedelta(days=1))
    assert len(seen) == 2
    # Without max_age the copy is good forever, however old.
    os.utime(cached, (two_days_ago, two_days_ago))
    client.get(url)
    assert len(seen) == 2
