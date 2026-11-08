"""The demo switch: live runs are on only while now < DEMO_LIVE_UNTIL.

On AWS the time lives in one SSM parameter that `make demo-on` moves forward; the
API reads it at most once a minute. Locally it comes from the DEMO_LIVE_UNTIL
setting. Anything missing or unreadable means off.
"""

import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from knock_on_atlas.config import Settings

CACHE_SECONDS = 60
log = logging.getLogger(__name__)


def parse_until(value: str) -> datetime | None:
    try:
        until = datetime.fromisoformat(value)
    except ValueError:
        return None
    # A time without a zone is ambiguous: treat it as off rather than guess.
    return until if until.tzinfo is not None else None


class LiveGate:
    def __init__(
        self,
        param: str | None,
        until: datetime | None,
        ssm: Any = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.param = param
        self.until = until
        self.ssm = ssm
        self.clock = clock
        self.fetched_at: float | None = None

    def live_until(self) -> datetime | None:
        if self.param is None:
            return self.until
        now = self.clock()
        if self.fetched_at is None or now - self.fetched_at >= CACHE_SECONDS:
            try:
                response = self.ssm.get_parameter(Name=self.param)
            except (BotoCoreError, ClientError):
                log.warning(
                    "cannot read %s; live runs are off", self.param, exc_info=True
                )
                return None  # not cached: the next request tries again
            self.until = parse_until(response["Parameter"]["Value"])
            self.fetched_at = now
        return self.until

    def is_live(self) -> bool:
        until = self.live_until()
        return until is not None and self.clock() < until.timestamp()


def gate_from_settings(settings: Settings) -> LiveGate:
    param = settings.demo_live_param
    ssm = boto3.client("ssm") if param else None
    return LiveGate(param, settings.demo_live_until, ssm)
