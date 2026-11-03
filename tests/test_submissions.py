from datetime import UTC, date, datetime

from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.edgar.submissions import (
    Filing8K,
    company_profile,
    material_8ks,
    recent_8ks,
)


def test_keeps_only_original_8ks(sec_client: SecClient) -> None:
    filings = recent_8ks(sec_client, "320193")
    # The fixture also holds an 8-K/A, a 10-Q and a Form 4.
    assert [f.accession for f in filings] == [
        "0000320193-26-000018",
        "0000320193-26-000011",
    ]


def test_parses_items_dates_and_url(sec_client: SecClient) -> None:
    f = recent_8ks(sec_client, "0000320193")[0]
    assert f.cik == "0000320193"
    assert f.items == ("2.02", "9.01")
    assert f.filed == date(2026, 7, 30)
    assert f.accepted_at == datetime(2026, 7, 30, 20, 30, 28, tzinfo=UTC)
    assert f.url == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019326000018/aapl-20260730.htm"
    )


def test_company_profile_keeps_original_10k_dates(sec_client: SecClient) -> None:
    profile = company_profile(sec_client, 1278027)
    assert profile.name == "B&G Foods, Inc."
    assert (profile.sic, profile.sic_description) == (
        "2000",
        "Food and Kindred Products",
    )
    assert profile.tickers == ("BGS",)
    # The 10-K/A from 2007 and the 10-Qs are left out.
    assert profile.ten_k_filed == (
        date(2024, 2, 28),
        date(2025, 2, 25),
        date(2026, 3, 3),
    )


def eight_k(accession: str, filed: date, items: tuple[str, ...]) -> Filing8K:
    accepted = datetime(filed.year, filed.month, filed.day, 21, tzinfo=UTC)
    return Filing8K("0000104169", accession, filed, accepted, items, "x.htm")


def test_material_8ks_keep_material_items_since_a_date() -> None:
    filings = [
        eight_k("a-1", date(2026, 2, 19), ("2.02", "9.01")),  # earnings only
        eight_k("a-2", date(2026, 1, 5), ("1.01", "9.01")),
        eight_k("a-3", date(2025, 10, 30), ("2.05",)),  # before the cut
        eight_k("a-4", date(2025, 11, 1), ("8.01",)),
        eight_k("a-5", date(2026, 3, 2), ()),
    ]
    kept = material_8ks(filings, date(2025, 11, 1))
    assert [f.accession for f in kept] == ["a-2", "a-4"]
