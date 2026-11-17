"""koa-api: the FastAPI app behind a function URL."""

from functools import cache
from typing import Any

from mangum import Mangum

from knock_on_atlas.api.app import create_app
from knock_on_atlas.config import get_settings
from knock_on_atlas.lambdas.secrets import load_secrets
from knock_on_atlas.observability import configure


@cache
def app() -> Mangum:
    load_secrets()
    configure(get_settings())
    return Mangum(create_app(), lifespan="off")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return app()(event, context)
