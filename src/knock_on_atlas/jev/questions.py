"""Every Jev question, its wording and the thresholds that read its answers."""

from dataclasses import dataclass
from typing import Any

from knock_on_atlas.graph_store.resolve import Candidate, Hub
from knock_on_atlas.jev.client import (
    ChoiceAnswer,
    ChoiceQuestion,
    JevCall,
    JevClient,
    JevError,
    NoulAnswer,
    NoulCriteria,
    NoulQuestion,
    Question,
    ScoreAnswer,
    ScoreQuestion,
)

# J3 entity resolution. A companion Noul says whether the label names any candidate
# (TypeSafe advises it over a "none of these" option); the Choice is asked twice,
# candidates in score order and reversed, to catch first-option bias.
J3_NAMES = "j3_names_candidate"
J3_PICK = "j3_pick"
J3_PICK_REVERSED = "j3_pick_reversed"
J3_NAMES_MIN = 0.3  # below: the label names none of the candidates
J3_APPROVE_MIN = 0.85  # spec J3: below goes to the curation queue

J3_PICK_INSTRUCTIONS = "Which company in `candidates` does `customer_label` refer to?"


@dataclass(frozen=True)
class EntityDecision:
    hub: Hub | None
    confidence: float
    method: str  # jev_choice, or fuzzy when Jev is off


def entity_state(label: str, supplier: str, cands: list[Candidate]) -> dict[str, Any]:
    return {
        "supplier": supplier,
        "customer_label": label,
        "candidates": {c.hub.name: list(c.hub.aliases) for c in cands},
    }


def entity_questions(cands: list[Candidate]) -> dict[str, Question]:
    def criteria(order: list[Candidate]) -> dict[str, str | None]:
        # Names only: their other names are in `candidates`, read by every question.
        return {c.hub.name: None for c in order}

    return {
        J3_NAMES: NoulQuestion(
            instructions=(
                "Does `customer_label` name one specific company in `candidates`?"
            ),
            criteria=NoulCriteria(
                true="The label names one candidate company, or a subsidiary, division"
                " or store brand that company owns",
                false="The label names a company not in the list, several companies"
                " combined such as 'X and Y', everyone except a company such as"
                " 'other than X', or a kind of customer such as wholesale customers"
                " or health plans",
            ),
        ),
        J3_PICK: ChoiceQuestion(
            instructions=J3_PICK_INSTRUCTIONS, criteria=criteria(cands)
        ),
        J3_PICK_REVERSED: ChoiceQuestion(
            instructions=J3_PICK_INSTRUCTIONS, criteria=criteria(cands[::-1])
        ),
    }


def decide_entity(cands: list[Candidate], call: JevCall) -> EntityDecision:
    answers = call.response.answers
    names, pick, reversed_pick = (
        answers[J3_NAMES],
        answers[J3_PICK],
        answers[J3_PICK_REVERSED],
    )
    if not (
        isinstance(names, NoulAnswer)
        and isinstance(pick, ChoiceAnswer)
        and isinstance(reversed_pick, ChoiceAnswer)
    ):
        raise JevError(f"J3 answers have unexpected types: {answers}")
    if names.noul < J3_NAMES_MIN:
        return EntityDecision(None, 1 - names.noul, "jev_choice")
    hub = next(c.hub for c in cands if c.hub.name == pick.choice)
    # Disagreement between the two orders caps confidence below approval.
    confidence = min(
        names.noul,
        pick.probabilities[pick.choice],
        reversed_pick.probabilities.get(pick.choice, 0.0),
    )
    return EntityDecision(hub, confidence, "jev_choice")


def choose_entity(
    jev: JevClient | None, label: str, supplier: str, cands: list[Candidate]
) -> tuple[EntityDecision, JevCall | None]:
    if jev is None:
        # Stub: Jev off, so the top fuzzy hub waits for a reviewer at confidence 0.
        return EntityDecision(cands[0].hub, 0.0, "fuzzy"), None
    call = jev.ask(entity_state(label, supplier, cands), entity_questions(cands))
    return decide_entity(cands, call), call


# Relationship check, for edges the SIC rules leave open: is the revenue a purchase?
REL_BUYS = "rel_customer_buys"
REL_YES_MIN = 0.7
REL_NO_MAX = 0.3


def relationship_state(
    supplier: str,
    industry: str | None,
    customer: str,
    label: str,
    disclosure: str | None = None,
) -> dict[str, Any]:
    state = {
        "supplier": supplier,
        "supplier_industry": industry or "unknown",
        "customer": customer,
        "customer_as_disclosed": label,
    }
    # The supplier's own words about the customer, when the 10-K section is indexed:
    # names and an industry code alone can't tell a sale from a marketplace.
    if disclosure:
        state["disclosure"] = disclosure
    return state


def relationship_questions() -> dict[str, Question]:
    return {
        REL_BUYS: NoulQuestion(
            instructions="Does `customer` itself buy the goods or services that"
            " `supplier` sells?",
            criteria=NoulCriteria(
                true="The customer pays the supplier for products or services it uses"
                " or resells, such as a retailer stocking the supplier's goods or a"
                " distributor buying its drugs",
                false="The customer pays for services its own members receive, such"
                " as an insurer reimbursing tests, rents property from the supplier,"
                " or is a marketplace where shoppers buy the supplier's goods",
            ),
        )
    }


def relationship_flag(
    jev: JevClient | None, state: dict[str, Any]
) -> tuple[str | None, JevCall | None]:
    if jev is None:
        return "unchecked", None
    call = jev.ask(state, relationship_questions())
    answer = call.response.answers[REL_BUYS]
    if not isinstance(answer, NoulAnswer):
        raise JevError(f"relationship answer has unexpected type: {answer}")
    if answer.noul >= REL_YES_MIN:
        return None, call
    return ("not_a_customer" if answer.noul <= REL_NO_MAX else "unclear"), call


# J1 materiality of a free-text 8-K for the filer's suppliers (spec §12).
J1 = "j1_materiality"
J1_MIN = 0.80  # below: MONITOR, never a confident label


def materiality_state(filer: str, event: dict[str, Any]) -> dict[str, Any]:
    state = {"filer": filer, "items": event["items"], "item_text": event["excerpt"]}
    if event.get("exhibit_excerpt"):
        state["press_release"] = event["exhibit_excerpt"]
    return state


def materiality_questions() -> dict[str, Question]:
    return {
        J1: ChoiceQuestion(
            instructions="What does the news in this 8-K mean for how much `filer`"
            " buys from its suppliers?",
            criteria={
                "MATERIAL_NEGATIVE": "The filer cuts something its suppliers sell"
                " into: plant closures, production or program cuts, impairments of"
                " operations, store closures or lost demand",
                "MATERIAL_POSITIVE": "The filer expands something its suppliers sell"
                " into: new plants, higher production, large new contracts, or an"
                " acquisition that adds operations",
                "ROUTINE": "Financing, debt or share offerings, dividends, executive"
                " or board changes, shareholder votes, or other news that leaves"
                " the filer's purchasing unchanged",
                "NOT_SUPPLY_CHAIN": "Legal, regulatory, tax or accounting matters, or"
                " news about the filer's own customers and prices rather than its"
                " purchases",
            },
        )
    }


def materiality(jev: JevClient, state: dict[str, Any]) -> tuple[str, float, JevCall]:
    call = jev.ask(state, materiality_questions())
    answer = call.response.answers[J1]
    if not isinstance(answer, ChoiceAnswer):
        raise JevError(f"J1 answer has unexpected type: {answer}")
    return answer.choice, answer.probabilities[answer.choice], call


# J4 evidence sufficiency per claim, 0-4 (spec §12); below 3 fails the claim.
J4_PASS = 3
J4_LEVELS = [
    "The quote is about something else and says nothing about the two companies",
    "The quote mentions the customer or the supplier but not any business between them",
    "The quote describes business between them but not how much the supplier depends"
    " on the customer",
    "The quote says the customer is a major customer of the supplier, without a share"
    " of revenue",
    "The quote names the customer and gives its share of the supplier's revenue or"
    " sales",
]


def sufficiency_questions(
    claims: dict[str, tuple[str, str, str]],
) -> dict[str, Question]:
    """One Score per claim id: (supplier, customer, quote)."""
    return {
        claim_id: ScoreQuestion(
            instructions=f"How well does this quote from {supplier}'s filing support"
            f" the claim that {customer} is a customer {supplier} depends on?\n"
            f"Quote: {quote}",
            criteria=J4_LEVELS,
        )
        for claim_id, (supplier, customer, quote) in claims.items()
    }


def sufficiency(
    jev: JevClient, claims: dict[str, tuple[str, str, str]]
) -> tuple[dict[str, int], JevCall]:
    """Each claim's level, the rounded position on the 0-4 scale."""
    state = {"source": "quotes are from the suppliers' own 10-K and 10-Q filings"}
    call = jev.ask(state, sufficiency_questions(claims))
    levels = {}
    for claim_id in claims:
        answer = call.response.answers[claim_id]
        if not isinstance(answer, ScoreAnswer):
            raise JevError(f"J4 answer has unexpected type: {answer}")
        levels[claim_id] = round(answer.score)
    return levels, call


# J5 escalation (spec §12), asked only when no rule already sends the brief to review.
J5 = "j5_escalation"
J5_MIN = 0.80  # below: human review


def escalation_questions() -> dict[str, Question]:
    return {
        J5: ChoiceQuestion(
            instructions="Can the effect of `filing` on `suppliers` be stated from"
            " `evidence` without a reviewer checking it first?",
            criteria={
                "AUTO_PUBLISH": "The filing's news is clear, and the quotes plainly"
                " show which suppliers sell to the filer",
                "HUMAN_REVIEW": "The news is ambiguous, unusual or disputed, or the"
                " quotes only partly show the suppliers' business with the filer",
            },
        )
    }


def escalation(jev: JevClient, state: dict[str, Any]) -> tuple[str, float, JevCall]:
    call = jev.ask(state, escalation_questions())
    answer = call.response.answers[J5]
    if not isinstance(answer, ChoiceAnswer):
        raise JevError(f"J5 answer has unexpected type: {answer}")
    return answer.choice, answer.probabilities[answer.choice], call
