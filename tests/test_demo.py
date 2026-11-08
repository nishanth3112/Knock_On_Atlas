from datetime import UTC, datetime

import boto3
import pytest
from botocore.stub import Stubber

from knock_on_atlas.api.demo import CACHE_SECONDS, LiveGate, parse_until

PARAM = "/koa/prod/DEMO_LIVE_UNTIL"
NOON = datetime(2026, 11, 8, 12, tzinfo=UTC).timestamp()


def ssm_returning(*values: str) -> tuple[object, Stubber]:
    client = boto3.client(
        "ssm",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    stub = Stubber(client)
    for value in values:
        stub.add_response(
            "get_parameter",
            {"Parameter": {"Name": PARAM, "Value": value}},
            {"Name": PARAM},
        )
    return client, stub


def test_ssm_is_read_at_most_once_a_minute() -> None:
    now = [NOON]
    client, stub = ssm_returning("2026-11-08T13:00:00Z", "2000-01-01T00:00:00Z")
    gate = LiveGate(PARAM, None, client, clock=lambda: now[0])
    with stub:
        assert gate.is_live() and gate.is_live()  # one read, then the cache
        now[0] += CACHE_SECONDS  # `make demo-off` happened meanwhile
        assert not gate.is_live()
        stub.assert_no_pending_responses()


def test_live_ends_at_the_stored_time() -> None:
    until = datetime(2026, 11, 8, 12, 30, tzinfo=UTC)
    assert LiveGate(None, until, clock=lambda: NOON).is_live()
    assert not LiveGate(None, until, clock=lambda: until.timestamp()).is_live()
    assert not LiveGate(None, None, clock=lambda: NOON).is_live()  # unset: off


@pytest.mark.parametrize("value", ["soon", "2026-11-08T13:00:00", ""])
def test_unreadable_or_zoneless_times_mean_off(value: str) -> None:
    assert parse_until(value) is None


def test_an_ssm_error_means_off_and_is_retried() -> None:
    client, stub = ssm_returning()
    stub.add_client_error("get_parameter", "ParameterNotFound")
    stub.add_response(
        "get_parameter",
        {"Parameter": {"Name": PARAM, "Value": "2026-11-08T13:00:00Z"}},
        {"Name": PARAM},
    )
    gate = LiveGate(PARAM, None, client, clock=lambda: NOON)
    with stub:
        assert not gate.is_live()
        assert gate.is_live()  # not cached after an error
