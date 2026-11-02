from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.edgar.tickers import load_ticker_map, pad_cik


def test_pad_cik() -> None:
    assert pad_cik(320193) == "0000320193"
    assert pad_cik("4127") == "0000004127"


def test_ticker_map(sec_client: SecClient) -> None:
    tickers = load_ticker_map(sec_client)
    assert tickers["AAPL"] == "0000320193"
    assert tickers["WMT"] == "0000104169"
    assert tickers["BRK-B"] == "0001067983"
    assert "MSFT" not in tickers
