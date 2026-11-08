import asyncio

import boto3
import pytest
from botocore.stub import Stubber

from knock_on_atlas.api.jobs import LocalQueue, SqsQueue, queue_from_settings
from knock_on_atlas.config import Settings
from knock_on_atlas.workflow.worker import Job

URL = "https://sqs.us-east-1.amazonaws.com/123456789012/koa-jobs"
JOB = Job(analysis_id="a1", hub_cik="0000037996", accession="0000037996-25-000238")


def test_sqs_queue_sends_the_job_as_json() -> None:
    client = boto3.client(
        "sqs",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    with Stubber(client) as stub:
        stub.add_response(
            "send_message",
            {"MessageId": "m1"},
            {"QueueUrl": URL, "MessageBody": JOB.model_dump_json()},
        )
        SqsQueue(client, URL).send(JOB)
        stub.assert_no_pending_responses()


def test_local_queue_keeps_a_failed_job_for_the_next_drain() -> None:
    queue = LocalQueue()
    second = JOB.model_copy(update={"analysis_id": "a2"})
    queue.send(JOB)
    queue.send(second)
    seen: list[str] = []

    async def flaky(job: Job) -> None:
        seen.append(job.analysis_id)
        if len(seen) == 1:
            raise RuntimeError("worker died")

    with pytest.raises(RuntimeError):
        asyncio.run(queue.drain(flaky))
    assert queue.jobs == [JOB, second]  # nothing lost
    assert asyncio.run(queue.drain(flaky)) == 2
    assert seen == ["a1", "a1", "a2"] and queue.jobs == []


def test_sqs_needs_a_queue_url() -> None:
    settings = Settings(
        _env_file=None, sec_user_agent="Test t@example.com", job_queue="sqs"
    )  # type: ignore[call-arg]
    with pytest.raises(RuntimeError, match="JOB_QUEUE_URL"):
        queue_from_settings(settings)
    local = Settings(_env_file=None, sec_user_agent="Test t@example.com")  # type: ignore[call-arg]
    assert isinstance(queue_from_settings(local), LocalQueue)
