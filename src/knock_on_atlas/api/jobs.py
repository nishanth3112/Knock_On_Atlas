"""Where the API puts worker jobs: SQS on AWS, an in-process list locally."""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import boto3

from knock_on_atlas.config import JobQueueKind, Settings
from knock_on_atlas.workflow.worker import Job


class JobQueue(Protocol):
    def send(self, job: Job) -> None: ...


class SqsQueue:
    def __init__(self, client: Any, url: str) -> None:
        self.client = client
        self.url = url

    def send(self, job: Job) -> None:
        self.client.send_message(QueueUrl=self.url, MessageBody=job.model_dump_json())


class LocalQueue:
    """Jobs wait until drained, like messages waiting for the worker Lambda."""

    def __init__(self) -> None:
        self.jobs: list[Job] = []

    def send(self, job: Job) -> None:
        self.jobs.append(job)

    async def drain(self, handle: Callable[[Job], Awaitable[object]]) -> int:
        done = 0
        while self.jobs:
            await handle(self.jobs[0])
            # Removed only after it succeeds: a failed job stays, as SQS redelivers.
            self.jobs.pop(0)
            done += 1
        return done


def queue_from_settings(settings: Settings) -> JobQueue:
    if settings.job_queue is JobQueueKind.SQS:
        if not settings.job_queue_url:
            raise RuntimeError("JOB_QUEUE=sqs needs JOB_QUEUE_URL")
        return SqsQueue(boto3.client("sqs"), settings.job_queue_url)
    return LocalQueue()
