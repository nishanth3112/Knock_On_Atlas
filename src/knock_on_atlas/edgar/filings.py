import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser

from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.edgar.submissions import PROFILE_MAX_AGE
from knock_on_atlas.edgar.tickers import pad_cik

# Item number -> section name kept for search, per form.
SECTIONS = {
    "10-K": {"1": "item_1", "1A": "item_1a", "7": "item_7"},
    "10-Q": {"2": "item_2"},
}
# Shorter spans are table-of-contents lines or "Not applicable", not the section.
MIN_SECTION_CHARS = 1000

_ITEM_HEADING = re.compile(r"(?i)^item\s*(\d+[a-c]?)\b")
_BLOCK_TAGS = {
    "p", "div", "br", "tr", "li", "table", "section",
    "h1", "h2", "h3", "h4", "h5", "h6",
}  # fmt: skip
# ix:header holds inline XBRL's hidden facts, which are not part of the text.
_HIDDEN_TAGS = {"script", "style", "ix:header"}


@dataclass(frozen=True)
class Filing:
    cik: str
    accession: str
    form: str
    filed: date
    period: date | None  # the report date; blank in submissions for a few forms
    primary_document: str

    @property
    def url(self) -> str:
        folder = self.accession.replace("-", "")
        return (
            f"https://www.sec.gov/Archives/edgar/data/{int(self.cik)}/"
            f"{folder}/{self.primary_document}"
        )


def recent_filings(
    client: SecClient, cik: int | str, forms: set[str], since: date
) -> list[Filing]:
    padded = pad_cik(cik)
    recent = client.get_json(
        f"https://data.sec.gov/submissions/CIK{padded}.json", PROFILE_MAX_AGE
    )["filings"]["recent"]
    filings = [
        Filing(
            cik=padded,
            accession=recent["accessionNumber"][i],
            form=form,
            filed=date.fromisoformat(recent["filingDate"][i]),
            period=(
                date.fromisoformat(recent["reportDate"][i])
                if recent["reportDate"][i]
                else None
            ),
            primary_document=recent["primaryDocument"][i],
        )
        for i, form in enumerate(recent["form"])
        if form in forms
    ]
    return [f for f in filings if f.filed >= since]


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _HIDDEN_TAGS:
            self._hidden += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _HIDDEN_TAGS:
            self._hidden = max(0, self._hidden - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


def html_to_lines(document: bytes) -> list[str]:
    """Visible text, one non-empty line per block element, whitespace collapsed."""
    parser = _TextParser()
    parser.feed(document.decode("utf-8", errors="replace"))
    text = "".join(parser.parts).replace("\xa0", " ")
    return [line for raw in text.split("\n") if (line := " ".join(raw.split()))]


def extract_sections(lines: list[str], form: str) -> dict[str, str]:
    """Section name -> text, for the items of SECTIONS that the filing has.

    Each item heading appears in the table of contents and again at the section
    itself; the occurrence followed by the most text, up to the next item heading,
    is the section.
    """
    heads: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = _ITEM_HEADING.match(line)
        # Some filers repeat the heading on every page ("Item 1. Business
        # (Continued)"); a heading for the item already open continues it.
        if m and (not heads or heads[-1][1] != m.group(1).upper()):
            heads.append((i, m.group(1).upper()))
    # Each heading's span ends where the next one starts (the last one at the end).
    ends = [i for i, _ in heads[1:]] + [len(lines)] if heads else []
    found = {}
    for item, name in SECTIONS[form].items():
        spans = [
            "\n".join(lines[start:end])
            for (start, head), end in zip(heads, ends, strict=True)
            if head == item
        ]
        best = max(spans, key=len, default="")
        if len(best) >= MIN_SECTION_CHARS:
            found[name] = best
    return found
