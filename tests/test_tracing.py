import logfire
from evals_support import load_cases, run_case
from logfire.testing import SimpleSpanProcessor, TestExporter

from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.retrieval.embed import Embedder


def test_a_run_traces_its_nodes_model_calls_and_tools(
    sec_client: SecClient, fake_embedder: Embedder
) -> None:
    exporter = TestExporter()
    # In memory only: the same spans observability.configure sends with a token.
    logfire.configure(
        send_to_logfire=False,
        console=False,
        additional_span_processors=[SimpleSpanProcessor(exporter)],
    )
    [case] = [c for c in load_cases() if c.id == "ambiguous-jev-negative"]
    assert run_case(case, sec_client, fake_embedder).problems == []

    spans = [
        s for s in exporter.exported_spans_as_dict() if s["name"] != "jev {question}"
    ]
    attrs = [s["attributes"] for s in spans]
    nodes = {a["node"] for a in attrs if "node" in a}
    assert {"triage", "planner", "verifier", "risk_gate", "brief_synthesizer"} <= nodes
    assert all(a["run_id"] == case.id for a in attrs if "node" in a)
    llm = [a for a in attrs if a.get("logfire.msg") == "llm call"]
    assert llm and all(a["tokens"] > 0 and a["model"] for a in llm)
    tools = {a["tool"]: a["ok"] for a in attrs if "tool" in a}
    assert tools["search_filings"] is True and tools["get_company"] is True
    # Every model and tool call sits under the node that made it.
    by_id = {s["context"]["span_id"]: s for s in spans}
    for span in spans:
        if "tool" in span["attributes"] or span["name"] == "llm call":
            parent = by_id.get(span["parent"]["span_id"]) if span["parent"] else None
            assert parent is not None and "node" in parent["attributes"]
    jev = [
        s["attributes"]
        for s in exporter.exported_spans_as_dict()
        if s["name"] == "jev {question}"
    ]
    assert {a["question"] for a in jev} == {"j1", "j4"}
    assert all(a["input_tokens"] == 100 for a in jev)
