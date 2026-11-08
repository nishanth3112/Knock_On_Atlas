"""The worker's job: start, resume or finish one run from its checkpoint.

Jobs arrive at least once (SQS redelivers after a crash or timeout), so the
checkpoint, not the job, decides what happens: a run is started only when it has
no checkpoint and no reviewer decision, resumed where it stopped, and
published idempotently.
"""

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command, StateSnapshot
from pydantic import BaseModel

from knock_on_atlas.workflow.analyses import Status, publish
from knock_on_atlas.workflow.deps import Deps
from knock_on_atlas.workflow.graph import build_graph, run_config, start


class Job(BaseModel):
    analysis_id: str  # also the checkpoint thread id
    hub_cik: str
    accession: str
    decision: dict[str, Any] | None = None  # a reviewer's answer to a paused run


def outcome(snapshot: StateSnapshot) -> Status:
    values = snapshot.values
    if snapshot.interrupts:
        return "paused"
    if "brief" in values:
        return "published"
    if "review" in values and values["review"].decision == "rejected":
        return "rejected"
    return "ended"


async def handle(job: Job, deps: Deps, saver: BaseCheckpointSaver[Any]) -> Status:
    """Run the job; any exception leaves the message for redelivery."""
    graph = build_graph(deps, saver)
    config = run_config(job.analysis_id)
    snapshot = await graph.aget_state(config)

    async def run(arg: Any) -> StateSnapshot:
        # "sync" saves each step before the next starts, so a crash loses one step.
        await graph.ainvoke(arg, config, durability="sync")
        return await graph.aget_state(config)

    if not snapshot.values:
        # A decision with no checkpoint (expired, or a wrong id) must not rerun it.
        if job.decision is None:
            snapshot = await run(start(job.analysis_id, job.hub_cik, job.accession))
    elif snapshot.interrupts:
        if job.decision is not None:
            snapshot = await run(Command(resume=job.decision))
    elif snapshot.next:
        snapshot = await run(None)  # crashed mid-run: continue from the checkpoint
    status = outcome(snapshot)
    brief = snapshot.values.get("brief")
    publish(
        deps.conn,
        job.analysis_id,
        job.model_dump(include={"hub_cik", "accession"}),
        status,
        brief.model_dump(mode="json") if brief is not None else None,
    )
    deps.crash("publish:after")  # published, but the job is not yet acknowledged
    return status
