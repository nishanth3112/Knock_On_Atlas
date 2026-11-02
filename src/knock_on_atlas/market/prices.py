from datetime import date

import httpx

from knock_on_atlas.ratelimit import RateLimiter

# Basic plan: 8 credits per minute, one credit per single-symbol request.
TWELVE_DATA_REQUESTS_PER_SECOND = 8 / 60


class TwelveDataError(RuntimeError):
    pass


class TwelveDataClient:
    def __init__(
        self, api_key: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._http = httpx.Client(
            base_url="https://api.twelvedata.com",
            params={"apikey": api_key},
            transport=transport,
            timeout=30,
        )
        self._limiter = RateLimiter(TWELVE_DATA_REQUESTS_PER_SECOND)

    def daily_closes(self, symbol: str, start: date, end: date) -> dict[date, float]:
        self._limiter.wait()
        response = self._http.get(
            "/time_series",
            params={
                "symbol": symbol,
                "interval": "1day",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "outputsize": 5000,
                "order": "asc",
            },
        )
        body = response.json()
        # Messages are built here, not by raise_for_status, whose text includes the
        # URL and therefore the API key.
        if response.is_error or body.get("status") != "ok":
            raise TwelveDataError(
                f"{symbol}: HTTP {response.status_code} {body.get('message', '')}"
            )
        bars = sorted(body["values"], key=lambda bar: bar["datetime"])
        return {date.fromisoformat(b["datetime"]): float(b["close"]) for b in bars}
