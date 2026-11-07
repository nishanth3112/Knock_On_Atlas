"""Where run checkpoints live: memory for tests, DynamoDB so a run can resume in
another process (spec §20)."""

from contextlib import suppress
from typing import Any

import boto3
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph_checkpoint_aws import DynamoDBSaver

from knock_on_atlas.config import Checkpointer, Settings
from knock_on_atlas.workflow.graph import checkpoint_serde

TABLE = "knock-on-atlas-checkpoints"
# Long enough for a paused run to wait for its reviewer.
TTL_SECONDS = 30 * 24 * 3600
# DynamoDB Local accepts any credentials; never reach for the developer's AWS keys.
LOCAL_SESSION = {
    "aws_access_key_id": "local",
    "aws_secret_access_key": "local",
    "region_name": "us-east-1",
}


class AllowlistDynamoDBSaver(DynamoDBSaver):
    # The saver takes no serde argument; its __init__ picks up this class default.
    serde = checkpoint_serde()


def create_table(client: Any, table: str = TABLE) -> None:
    """For DynamoDB Local only; on AWS, Terraform owns the table."""
    with suppress(client.exceptions.ResourceInUseException):
        client.create_table(
            TableName=table,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
        )


def dynamodb_saver(endpoint_url: str | None, table: str = TABLE) -> DynamoDBSaver:
    session = boto3.Session(**LOCAL_SESSION) if endpoint_url else None
    saver = AllowlistDynamoDBSaver(
        table,
        session=session,
        endpoint_url=endpoint_url,
        ttl_seconds=TTL_SECONDS,
        # A redelivered job may land on another worker right after a crash.
        consistent_read=True,
    )
    if endpoint_url:
        create_table(saver.client, table)
    return saver


def checkpointer(settings: Settings) -> BaseCheckpointSaver[Any]:
    if settings.checkpointer is Checkpointer.DYNAMODB:
        return dynamodb_saver(settings.dynamodb_endpoint_url)
    return InMemorySaver(serde=checkpoint_serde())
