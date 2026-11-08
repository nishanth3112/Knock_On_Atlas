"""What one analysis run carries from node to node (spec §11.1)."""

import operator
from datetime import date
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel

from knock_on_atlas.mcp_server.server import EightK

TriageLabel = Literal[
    "MATERIAL_NEGATIVE", "MATERIAL_POSITIVE", "ROUTINE", "NOT_SUPPLY_CHAIN", "MONITOR"
]


class Usage(BaseModel):
    """Spend so far; parallel branches each add theirs, so it is summed, not set."""

    llm_calls: int = 0
    tool_calls: int = 0
    tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            llm_calls=self.llm_calls + other.llm_calls,
            tool_calls=self.tool_calls + other.tool_calls,
            tokens=self.tokens + other.tokens,
        )


class Triage(BaseModel):
    label: TriageLabel
    method: Literal["rule", "jev", "fallback"]
    probability: float | None = None
    reason: str


class Supplier(BaseModel):
    cik: str
    via_cik: str
    depth: int
    weight: float  # percent of via_cik's revenue at depth 1 (traversal.Exposure)
    edge_ids: list[int]


class Plan(BaseModel):
    hub_cik: str
    as_of: date
    suppliers: list[Supplier]
    queries: list[str]

    @property
    def supplier_ciks(self) -> list[str]:
        return sorted({s.cik for s in self.suppliers})


class ExposureEdge(BaseModel):
    supplier_cik: str
    name: str
    depth: int
    weight: float
    note: str


class Evidence(BaseModel):
    supplier_cik: str
    chunk_id: int
    quote: str
    url: str


class StudyRow(BaseModel):
    cik: str
    depth: int
    status: str
    day0: date | None
    car_0_1: float | None
    car_m1_5: float | None
    t_0_1: float | None
    t_m1_5: float | None


class ClaimCheck(BaseModel):
    supplier_cik: str
    chunk_id: int
    citation_ok: bool
    numbers_ok: bool
    score: int  # J4 sufficiency 0-4


class VerificationReport(BaseModel):
    ok: bool
    checks: list[ClaimCheck]
    failed_suppliers: list[str]


class JevDecision(BaseModel):
    question: str  # j1 | j4 | j5
    answer: str
    probability: float | None
    request_id: str | None
    input_tokens: int


class Gate(BaseModel):
    route: Literal["AUTO_PUBLISH", "HUMAN_REVIEW"]
    reasons: list[str]


class ReviewDecision(BaseModel):
    decision: Literal["approved", "rejected"]
    reviewer: str
    note: str = ""


class Claim(BaseModel):
    text: str
    supplier_ciks: list[str]
    citations: list[int]  # chunk ids of verified evidence


class ExposureBrief(BaseModel):
    headline: str
    claims: list[Claim]
    gaps: list[str]


class KOAState(TypedDict, total=False):
    run_id: str
    hub_cik: str
    hub_name: str
    accession: str
    event: EightK
    triage: Triage
    plan: Plan
    exposures: Annotated[list[ExposureEdge], operator.add]
    evidence: Annotated[list[Evidence], operator.add]
    market: list[StudyRow]
    verification: VerificationReport
    jev: Annotated[list[JevDecision], operator.add]
    gate: Gate
    review: ReviewDecision
    brief: ExposureBrief
    loops: int
    usage: Annotated[Usage, operator.add]
    errors: Annotated[list[str], operator.add]
