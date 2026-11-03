import math
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


def judge(hits: list[tuple[str, str, str]], passages: list[Passage]) -> list[bool]:
    """Relevance of each (accession, section, content) hit, best first.

    Chunks overlap, so one passage can sit in two chunks; only the first counts.
    """
    credited: set[Passage] = set()
    relevant = []
    for accession, section, content in hits:
        match = next(
            (
                p
                for p in passages
                if p not in credited
                and (p.accession, p.section) == (accession, section)
                and _norm(p.quote) in _norm(content)
            ),
            None,
        )
        if match:
            credited.add(match)
        relevant.append(match is not None)
    return relevant


def recall_at(relevant: list[bool], total: int, k: int) -> float:
    return sum(relevant[:k]) / total if total else 0.0


def hit_rate_at(relevant: list[bool], k: int) -> float:
    return 1.0 if any(relevant[:k]) else 0.0


def mrr_at(relevant: list[bool], k: int) -> float:
    return next((1 / rank for rank, r in enumerate(relevant[:k], 1) if r), 0.0)


def ndcg_at(relevant: list[bool], total: int, k: int) -> float:
    dcg = sum(1 / math.log2(rank + 1) for rank, r in enumerate(relevant[:k], 1) if r)
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(total, k) + 1))
    return dcg / ideal if ideal else 0.0
