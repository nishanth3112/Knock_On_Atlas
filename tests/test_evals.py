"""The eval suite (spec §17.2): every case in evals/cases/koa_v1, run and graded."""

import pytest
from evals_support import RESULTS, Case, load_cases, run_case

from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.retrieval.embed import Embedder


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c.id)
def test_case(case: Case, sec_client: SecClient, fake_embedder: Embedder) -> None:
    graded = run_case(case, sec_client, fake_embedder)
    RESULTS[case.id] = graded
    assert graded.problems == []
