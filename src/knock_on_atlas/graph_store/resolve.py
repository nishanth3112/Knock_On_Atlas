import csv
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from rapidfuzz import fuzz, process, utils

# WRatio on processed names. >= 80 goes to Jev; 70-79 is counted as a near miss.
FUZZY_MIN = 80
NEAR_MISS_MIN = 70
MAX_CANDIDATES = 20

# Without this, WRatio scores "Toyota Motor Company" 86 against "The Boeing Company".
_LEGAL_WORDS = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|companies|llc|ltd|limited"
    r"|plc|lp|the|and|of|stores|com|subsidiaries|affiliates)\b"
)
_MEMBER_SUFFIX = re.compile(r"\s*\[?Member\]?\.?$")


@dataclass(frozen=True)
class Hub:
    cik: str
    name: str
    aliases: tuple[str, ...]


class MatchKind(StrEnum):
    ALIAS_EXACT = "alias_exact"
    FUZZY = "fuzzy"
    NEAR_MISS = "near_miss"
    NONE = "none"


@dataclass(frozen=True)
class Candidate:
    hub: Hub
    score: float


@dataclass(frozen=True)
class Resolution:
    kind: MatchKind
    candidates: tuple[Candidate, ...]


def load_hubs(path: Path) -> list[Hub]:
    with path.open(newline="") as f:
        return [
            Hub(
                cik=row["cik"],
                name=row["name"],
                aliases=tuple(a for a in row["aliases"].split("|") if a),
            )
            for row in csv.DictReader(f)
        ]


def clean_label(label: str) -> str:
    # Labels the loader could not strip: "Target Member", "RTXMember", "X [Member].".
    return _MEMBER_SUFFIX.sub("", label).strip()


def _key(name: str) -> str:
    return "".join(utils.default_process(name).split())


def _fuzzy_form(name: str) -> str:
    return " ".join(_LEGAL_WORDS.sub(" ", utils.default_process(name)).split())


def top_candidates(
    label: str, hubs: list[Hub], limit: int = MAX_CANDIDATES
) -> list[Candidate]:
    """Hubs ranked by their best-scoring name or alias."""
    names = {(i, n): n for i, hub in enumerate(hubs) for n in (hub.name, *hub.aliases)}
    best: dict[int, float] = {}
    for _, score, (i, _) in process.extract(
        clean_label(label),
        names,
        scorer=fuzz.WRatio,
        processor=_fuzzy_form,
        limit=None,
    ):
        best.setdefault(i, score)
    ranked = sorted(best.items(), key=lambda item: -item[1])[:limit]
    return [Candidate(hubs[i], score) for i, score in ranked]


def resolve(label: str, hubs: list[Hub]) -> Resolution:
    key = _key(clean_label(label))
    for hub in hubs:
        if key in {_key(n) for n in (hub.name, *hub.aliases)}:
            return Resolution(MatchKind.ALIAS_EXACT, (Candidate(hub, 100.0),))
    cands = tuple(top_candidates(label, hubs))
    top = cands[0].score if cands else 0.0
    if top >= FUZZY_MIN:
        kind = MatchKind.FUZZY
    elif top >= NEAR_MISS_MIN:
        kind = MatchKind.NEAR_MISS
    else:
        kind = MatchKind.NONE
    return Resolution(kind, cands)
