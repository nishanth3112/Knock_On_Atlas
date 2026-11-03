import math

import pytest

from knock_on_atlas.retrieval.metrics import (
    Passage,
    hit_rate_at,
    judge,
    mrr_at,
    ndcg_at,
    parse_passages,
    recall_at,
)


def test_parse_passages() -> None:
    assert parse_passages("a-1|item_1|31% of net sales; a-2|item_7|cut orders|x") == [
        Passage("a-1", "item_1", "31% of net sales"),
        Passage("a-2", "item_7", "cut orders|x"),
    ]
    assert parse_passages("") == []


def test_judge_needs_filing_section_and_quote_and_credits_once() -> None:
    passages = [Passage("a-1", "item_1", "31% of  Net Sales")]
    hits = [
        ("a-1", "item_7", "Walmart was 31% of net sales"),  # wrong section
        ("a-1", "item_1", "Walmart was 31% of net sales."),
        ("a-1", "item_1", "overlap: 31% of net sales"),  # same passage again
        ("a-1", "item_1", "unrelated"),
    ]
    assert judge(hits, passages) == [False, True, False, False]


def test_metrics_on_a_hand_computed_ranking() -> None:
    relevant = [False, True, False, True]  # 2 of 3 labelled passages found
    assert recall_at(relevant, total=3, k=10) == pytest.approx(2 / 3)
    assert recall_at(relevant, total=3, k=2) == pytest.approx(1 / 3)
    assert mrr_at(relevant, k=10) == pytest.approx(1 / 2)
    assert mrr_at(relevant, k=1) == 0.0
    assert hit_rate_at(relevant, k=5) == 1.0
    assert hit_rate_at(relevant, k=1) == 0.0
    dcg = 1 / math.log2(3) + 1 / math.log2(5)
    ideal = 1 + 1 / math.log2(3) + 1 / math.log2(4)
    assert ndcg_at(relevant, total=3, k=10) == pytest.approx(dcg / ideal)


def test_metrics_with_nothing_labelled_are_zero() -> None:
    assert recall_at([], 0, 10) == ndcg_at([], 0, 10) == mrr_at([], 10) == 0.0
