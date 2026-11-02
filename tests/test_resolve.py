from pathlib import Path

import pytest

from knock_on_atlas.graph_store.resolve import (
    Hub,
    MatchKind,
    load_hubs,
    resolve,
    top_candidates,
)

HUBS = [
    Hub("0000104169", "Walmart Inc.", ("Wal-Mart", "Walmart Sams Club")),
    Hub("0000012927", "The Boeing Company", ("Boeing",)),
    Hub("0000101829", "RTX Corporation", ("RTX",)),
    Hub("0000027419", "Target Corporation", ("Target",)),
]


@pytest.mark.parametrize(
    "label",
    ["WAL MART", "Wal-Mart", "Walmart Inc. [Member]", "RTXMember", "Target Member"],
)
def test_alias_exact_ignores_case_punctuation_and_member_suffix(label: str) -> None:
    result = resolve(label, HUBS)
    assert result.kind == MatchKind.ALIAS_EXACT
    assert len(result.candidates) == 1


@pytest.mark.parametrize(
    ("label", "kind", "top"),
    [
        ("Sams Club", MatchKind.FUZZY, "Walmart Inc."),
        ("Booking", MatchKind.NEAR_MISS, "The Boeing Company"),
        # Shared legal words alone must not make a match.
        ("Toyota Motor Company", MatchKind.NONE, None),
        ("Chevron Corporation", MatchKind.NONE, None),
    ],
)
def test_fuzzy_bands(label: str, kind: MatchKind, top: str | None) -> None:
    result = resolve(label, HUBS)
    assert result.kind == kind
    if top:
        assert result.candidates[0].hub.name == top


def test_top_candidates_rank_each_hub_once() -> None:
    ranked = top_candidates("Walmart Sams Club stores", HUBS, limit=3)
    assert [c.hub.name for c in ranked][0] == "Walmart Inc."
    assert len({c.hub.cik for c in ranked}) == len(ranked) == 3
    assert ranked == sorted(ranked, key=lambda c: -c.score)


def test_load_hubs_splits_aliases(tmp_path: Path) -> None:
    path = tmp_path / "hubs.csv"
    path.write_text(
        "cik,name,aliases\n0000027419,Target Corporation,Target\n"
        '0000104169,Walmart Inc.,"Wal-Mart|Walmart, Inc."\n'
        "0000075362,PACCAR Inc,\n"
    )
    hubs = load_hubs(path)
    assert hubs[1].aliases == ("Wal-Mart", "Walmart, Inc.")
    assert hubs[2].aliases == ()
