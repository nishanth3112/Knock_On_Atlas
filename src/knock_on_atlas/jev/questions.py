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
