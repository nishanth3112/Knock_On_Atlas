"""Cold-start secrets: a function's SSM parameters, loaded as environment variables.

Terraform passes only parameter names (SSM_PARAMETERS, comma-separated), so values
never sit in Terraform state or the function's configuration. Each value lands under
the name's last segment, where Settings reads it.
"""

import os
from typing import Any

import boto3


def load_secrets(ssm: Any = None) -> None:
    names = [n for n in os.environ.get("SSM_PARAMETERS", "").split(",") if n]
    if not names:
        return
    reply = (ssm or boto3.client("ssm")).get_parameters(
        Names=names, WithDecryption=True
    )
    if reply["InvalidParameters"]:
        raise RuntimeError(f"missing SSM parameters: {reply['InvalidParameters']}")
    for parameter in reply["Parameters"]:
        os.environ[parameter["Name"].rsplit("/", 1)[-1]] = parameter["Value"]
