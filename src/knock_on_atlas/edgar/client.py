import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

from knock_on_atlas.ratelimit import RateLimiter

# SEC allows 10 requests per second; staying at half leaves headroom.
SEC_REQUESTS_PER_SECOND = 5


class SecClient:
    def __init__(
        self,
        user_agent: str,
        cache_dir: Path,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = httpx.Client(
            headers={"User-Agent": user_agent},
            transport=transport,
            timeout=30,
            follow_redirects=True,
        )
        self._limiter = RateLimiter(SEC_REQUESTS_PER_SECOND)
        self._cache_dir = cache_dir

    def get(self, url: str) -> bytes:
        path = self._cache_dir / hashlib.sha256(url.encode()).hexdigest()
        if path.exists():
            return path.read_bytes()
        self._limiter.wait()
        response = self._http.get(url)
        response.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write then rename so a crash never leaves a truncated cache entry.
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(response.content)
        tmp.replace(path)
        return response.content

    def get_json(self, url: str) -> Any:
        return json.loads(self.get(url))

    def download(self, url: str, dest: Path) -> Path:
        # Streams to disk: FS&N zips are ~300 MB, too big for get()'s in-memory body.
        if dest.exists():
            return dest
        self._limiter.wait()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        with self._http.stream("GET", url) as response:
            response.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in response.iter_bytes(1 << 20):
                    f.write(chunk)
        tmp.replace(dest)
        return dest
