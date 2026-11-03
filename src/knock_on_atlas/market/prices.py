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
            # A header, not the apikey query param: httpx logs request URLs at INFO.
            headers={"Authorization": f"apikey {api_key}"},
            transport=transport,
            timeout=30,
        )
        self._limiter = RateLimiter(TWELVE_DATA_REQUESTS_PER_SECOND)

    def daily_closes(self, symbol: str, start: date, end: date) -> dict[date, float]:
        self._limiter.wait()
        response = self._http.get(
            "/time_series",
            params={
                # SEC share classes use a dash (MOG-A); Twelve Data a dot (MOG.A).
                "symbol": symbol.replace("-", "."),
                "interval": "1day",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "outputsize": 5000,
            },
        )
        is_json = response.headers.get("content-type", "").startswith(
            "application/json"
        )
        body = response.json() if is_json else {}
        if response.is_error or body.get("status") != "ok":
            raise TwelveDataError(
                f"{symbol}: HTTP {response.status_code} {body.get('message', '')}"
            )
        bars = sorted(body["values"], key=lambda bar: bar["datetime"])
        return {date.fromisoformat(b["datetime"]): float(b["close"]) for b in bars}
