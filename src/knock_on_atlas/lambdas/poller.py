"""koa-poller: the scheduled jobs, run by EventBridge Scheduler.

With the demo off it returns before opening a database connection, so Neon's
compute stays asleep between demos.
"""

import logging
import tempfile
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from typing import Any

import psycopg

from knock_on_atlas.api.demo import LiveGate, gate_from_settings
from knock_on_atlas.config import Settings, get_settings
from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.ingest import poll, refresh_studies
from knock_on_atlas.lambdas.secrets import load_secrets
from knock_on_atlas.market.prices import TwelveDataClient
from knock_on_atlas.observability import configure

log = logging.getLogger(__name__)
# A week back, so a filing missed by a failed run is picked up by the next one.
LOOKBACK = timedelta(days=7)


@cache
def runtime() -> tuple[Settings, LiveGate]:
    load_secrets()
    settings = get_settings()
    configure(settings)
    return settings, gate_from_settings(settings)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    settings, gate = runtime()
    job = event["job"]
    if not gate.is_live():
        log.info("demo off: %s skipped", job)
        return {"job": job, "skipped": "demo off"}
    url = settings.database_url.get_secret_value()
    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as conn:
        if job == "edgar-poll":
            # A fresh cache each run: a warm container must not serve day-old lists.
            with tempfile.TemporaryDirectory() as cache_dir:
                sec = SecClient(settings.sec_user_agent, Path(cache_dir))
                added = poll(conn, sec, date.today() - LOOKBACK)
            return {"job": job, "added": added}
        if job == "refresh-studies":
            if settings.twelvedata_api_key is None:
                raise RuntimeError("refresh-studies needs TWELVEDATA_API_KEY")
            key = settings.twelvedata_api_key.get_secret_value()
            done = refresh_studies(conn, TwelveDataClient(key))
            return {"job": job, "credits": done.credits, "failed": done.failed}
    raise ValueError(f"unknown job {job!r}")
