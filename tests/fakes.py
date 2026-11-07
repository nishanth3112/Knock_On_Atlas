"""Scripted chat-model replies and Jev answers for agent tests."""

import json
from collections.abc import Callable
from datetime import date
from itertools import count
from typing import Any

import httpx
from langchain_core.messages import AIMessage, BaseMessage

from knock_on_atlas.agents.llm import FakeChatModel
from knock_on_atlas.config import JevRoute
from knock_on_atlas.graph_store.edges import Edge
from knock_on_atlas.jev.client import JevClient

_ids = count()


def call(name: str, **args: Any) -> AIMessage:
    """A reply that calls one tool."""
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": f"c{next(_ids)}"}]
    )


def scripted(
    *replies: AIMessage | Callable[[list[BaseMessage]], AIMessage],
) -> FakeChatModel:
    """Replies in order; a callable reply is built from the conversation so far."""
    queue = list(replies)
    seen: list[list[BaseMessage]] = []

    def respond(messages: list[BaseMessage], tools: list[str]) -> AIMessage:
        seen.append(list(messages))
        reply = queue.pop(0)
        return reply if isinstance(reply, AIMessage) else reply(messages)

    model = FakeChatModel(responder=respond)
    model.__dict__["seen"] = seen  # conversations, for assertions
    return model


def fake_jev(
    answers: dict[str, dict[str, Any]], seen: list[Any] | None = None
) -> JevClient:
    """A Jev client that answers each question id from `answers`."""

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if seen is not None:
            seen.append(body)
        asked = {q: answers[q] for q in body["questions"]}
        usage = {"input_tokens": 100, "output_tokens": 0}
        reply = {"model": body["model"], "answers": asked, "usage": usage}
        return httpx.Response(200, json=reply)

    return JevClient(
        JevRoute.TYPESAFE, "test-key", transport=httpx.MockTransport(respond)
    )


def choice(option: str, p: float, others: tuple[str, ...] = ()) -> dict[str, Any]:
    rest = {o: (1 - p) / len(others) for o in others} if others else {}
    return {
        "type": "choice",
        "choice": option,
        "confidence": p,
        "probabilities": {option: p, **rest},
    }


def approved_edge(src: str, dst: str, pct: float) -> Edge:
    """An approved SUPPLIES_TO edge from supplier `src` to customer `dst`."""
    return Edge(
        src_cik=src,
        dst_cik=dst,
        pct=pct,
        period_end=date(2024, 12, 31),
        method="xbrl",
        extraction_method="alias_exact",
        member_label=dst,
        total_share=True,
        relationship_flag=None,
        source_accession=f"{src}-{dst}",
        confidence=1.0,
        status="approved",
        valid_from=date(2025, 1, 1),
        valid_to=None,
    )
