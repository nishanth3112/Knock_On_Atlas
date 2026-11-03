from dataclasses import dataclass
from datetime import date, datetime, timedelta

from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.edgar.tickers import pad_cik

# Submissions change with every filing: a new 10-K ends edges, a new 8-K is an event.
PROFILE_MAX_AGE = timedelta(days=1)
# 8-K items that can move a supplier's revenue; used to draft eval events.
MATERIAL_ITEMS = frozenset(
    {"1.01", "1.02", "1.03", "2.01", "2.05", "2.06", "4.02", "8.01"}
)


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
    recent = client.get_json(url, PROFILE_MAX_AGE)["filings"]["recent"]
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


def material_8ks(filings: list[Filing8K], since: date) -> list[Filing8K]:
    return [f for f in filings if f.filed >= since and MATERIAL_ITEMS & set(f.items)]


@dataclass(frozen=True)
class CompanyProfile:
    cik: str
    name: str
    sic: str | None
    sic_description: str | None
    tickers: tuple[str, ...]
    ten_k_filed: tuple[date, ...]  # ascending; amendments excluded


def company_profile(client: SecClient, cik: int | str) -> CompanyProfile:
    padded = pad_cik(cik)
    data = client.get_json(
        f"https://data.sec.gov/submissions/CIK{padded}.json", PROFILE_MAX_AGE
    )
    recent = data["filings"]["recent"]
    return CompanyProfile(
        cik=padded,
        name=data["name"],
        sic=data["sic"] or None,
        sic_description=data["sicDescription"] or None,
        tickers=tuple(data["tickers"]),
        ten_k_filed=tuple(
            sorted(
                date.fromisoformat(recent["filingDate"][i])
                for i, form in enumerate(recent["form"])
                if form == "10-K"
            )
        ),
    )
