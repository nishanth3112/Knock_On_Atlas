import math
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Passage:
    """A labelled relevant passage: a quote from one section of one filing."""

    accession: str
    section: str
    quote: str


def parse_passages(field: str) -> list[Passage]:
    """`accession|section|quote` entries separated by `;`."""
    passages = []
    for entry in field.split(";"):
        if entry.strip():
            accession, section, quote = (part.strip() for part in entry.split("|", 2))
            passages.append(Passage(accession, section, quote))
    return passages


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def judge(hits: list[tuple[str, str, str]], passages: list[Passage]) -> list[int]:
    """New labelled passages each (accession, section, content) hit covers, best first.

    Chunks overlap, so one passage can sit in two chunks; only the first counts.
    One chunk can also hold two passages, and then both count.
    """
    credited: set[Passage] = set()
    covered = []
    for accession, section, content in hits:
        found = {
            p
            for p in passages
            if p not in credited
            and (p.accession, p.section) == (accession, section)
            and _norm(p.quote) in _norm(content)
        }
        credited |= found
        covered.append(len(found))
    return covered


def recall_at(relevant: Sequence[int], total: int, k: int) -> float:
    return sum(relevant[:k]) / total if total else 0.0


def hit_rate_at(relevant: Sequence[int], k: int) -> float:
    return 1.0 if any(relevant[:k]) else 0.0


def mrr_at(relevant: Sequence[int], k: int) -> float:
    return next((1 / rank for rank, r in enumerate(relevant[:k], 1) if r), 0.0)


def ndcg_at(relevant: Sequence[int], total: int, k: int) -> float:
    # Gain 1 per relevant hit, so a chunk covering two passages can't pass 1.0.
    dcg = sum(1 / math.log2(rank + 1) for rank, r in enumerate(relevant[:k], 1) if r)
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(total, k) + 1))
    return dcg / ideal if ideal else 0.0
