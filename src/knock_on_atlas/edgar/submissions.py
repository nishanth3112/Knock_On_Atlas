from dataclasses import dataclass
from datetime import date, datetime

from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.edgar.tickers import pad_cik


@dataclass(frozen=True)
class Filing8K:
    cik: str
    accession: str
    filed: date
    accepted_at: datetime
    items: tuple[str, ...]
    primary_document: str

    @property
    def url(self) -> str:
        folder = self.accession.replace("-", "")
        return (
            f"https://www.sec.gov/Archives/edgar/data/{int(self.cik)}/"
            f"{folder}/{self.primary_document}"
        )


def recent_8ks(client: SecClient, cik: int | str) -> list[Filing8K]:
    padded = pad_cik(cik)
    url = f"https://data.sec.gov/submissions/CIK{padded}.json"
    recent = client.get_json(url)["filings"]["recent"]
    return [
        Filing8K(
            cik=padded,
            accession=recent["accessionNumber"][i],
            filed=date.fromisoformat(recent["filingDate"][i]),
            # The "Z" suffix is real UTC: Apple's 4:30 pm ET release reads 20:30Z.
            accepted_at=datetime.fromisoformat(recent["acceptanceDateTime"][i]),
            items=tuple(recent["items"][i].split(",")) if recent["items"][i] else (),
            primary_document=recent["primaryDocument"][i],
        )
        # Amendments (8-K/A) restate earlier events, so they are not new triggers.
        for i, form in enumerate(recent["form"])
        if form == "8-K"
    ]
