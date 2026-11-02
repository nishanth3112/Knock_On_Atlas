from datetime import UTC, date, datetime

from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.edgar.submissions import company_profile, recent_8ks


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
