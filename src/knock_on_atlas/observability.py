"""Tracing (spec §18): OpenTelemetry spans through Logfire, sent only with a token.

Without LOGFIRE_TOKEN nothing leaves the process, and an unconfigured process (tests,
CI) makes no spans at all.
"""

import logfire

from knock_on_atlas.config import Settings


def configure(settings: Settings) -> None:
    token = settings.logfire_token
    logfire.configure(
        send_to_logfire="if-token-present",
        token=token.get_secret_value() if token else None,
        service_name="knock-on-atlas",
        console=False,
        inspect_arguments=False,  # span names are templates, never f-strings
    )
