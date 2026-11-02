"""Record one live J3 request and its raw responses on both Jev routes.

Writes tests/fixtures/jev/j3_request.json and j3_<route>.json. Costs about 3,000
input tokens. Needs TYPESAFE_API_KEY and OPENROUTER_API_KEY.

Usage: uv run python scripts/record_jev_fixtures.py
"""

import json
from pathlib import Path

import httpx

from knock_on_atlas.config import JevRoute, get_settings
from knock_on_atlas.graph_store.resolve import load_hubs, top_candidates
from knock_on_atlas.jev.client import JevClient
from knock_on_atlas.jev.questions import entity_questions, entity_state

OUT = Path("tests/fixtures/jev")
# Public SEC data: Innovative Food Holdings' FS&N customer member label.
LABEL, SUPPLIER = "Sams Club Member", "INNOVATIVE FOOD HOLDINGS INC"


class Recorder(httpx.HTTPTransport):
    body = b""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = super().handle_request(request)
        self.body = response.read()
        return response


settings = get_settings()
cands = top_candidates(LABEL, load_hubs(Path("data/hubs.csv")))
keys = {
    JevRoute.TYPESAFE: settings.typesafe_api_key,
    JevRoute.OPENROUTER: settings.openrouter_api_key,
}
for route, key in keys.items():
    if key is None:
        raise SystemExit(f"{route}: API key not set")
    recorder = Recorder()
    call = JevClient(route, key.get_secret_value(), transport=recorder).ask(
        entity_state(LABEL, SUPPLIER, cands), entity_questions(cands)
    )
    request = call.request.model_dump(mode="json", exclude_none=True)
    request["model"] = "<route model>"
    (OUT / "j3_request.json").write_text(json.dumps(request, indent=2) + "\n")
    (OUT / f"j3_{route}.json").write_text(
        json.dumps(json.loads(recorder.body), indent=2) + "\n"
    )
    print(route, call.response.model, call.response.usage.input_tokens, call.request_id)
