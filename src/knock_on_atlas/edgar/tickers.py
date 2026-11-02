from knock_on_atlas.edgar.client import SecClient

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def pad_cik(cik: int | str) -> str:
    return str(int(cik)).zfill(10)


def load_ticker_map(client: SecClient) -> dict[str, str]:
    rows = client.get_json(TICKERS_URL).values()
    return {row["ticker"]: pad_cik(row["cik_str"]) for row in rows}
