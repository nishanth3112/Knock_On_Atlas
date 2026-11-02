import zipfile
from pathlib import Path

import httpx
import polars as pl
import pytest

from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.edgar.fsn import PLACEHOLDER_MEMBER, concentration_facts, load_month

# Real member names from the 2026-03 data set.
PLACEHOLDERS = [
    "CustomerA", "CustomerOne", "Customer1", "OneCustomer", "TopTenCustomers",
    "MajorCustomerTwo", "FiveLargestCustomers", "OnePurchaser", "CompanyA",
    "NoCustomer", "CarrierB", "AllOther", "Customers",
]  # fmt: skip
NAMED = [
    "WalMart", "AmazoncomInc", "PepsicoInc", "CustomerDetroitDiesel", "FordAccount",
    "AmazonSalesPlatform", "Att", "UNFI", "Cencora",
]  # fmt: skip


def test_placeholder_members() -> None:
    names = pl.Series(PLACEHOLDERS + NAMED)
    flagged = names.filter(names.str.contains(PLACEHOLDER_MEMBER)).to_list()
    assert flagged == PLACEHOLDERS


CUSTOMER_REVENUE = (
    "ConcentrationRiskByBenchmark=SalesRevenueNet;"
    "ConcentrationRiskByType=CustomerConcentrationRisk;MajorCustomers=WalMart;"
)
NUM = [
    "adsh\ttag\tversion\tddate\tqtrs\tuom\tdimh\tiprx\tvalue\tfootnote\tfootlen\tdimn\tcoreg\tdurp\tdatp\tdcml",
    # kept: named customer, revenue benchmark
    "0000000001-26-000001\tConcentrationRiskPercentage1\tus-gaap/2025\t20251231\t4\tpure\t0xa\t0\t0.3100\t\t0\t3\t\t0\t0\t4",
    # dropped as a repeat: same fact reported again (only iprx and footnote differ)
    "0000000001-26-000001\tConcentrationRiskPercentage1\tus-gaap/2025\t20251231\t4\tpure\t0xa\t1\t0.3100\tfootnote2\t9\t3\t\t0\t0\t4",
    # kept: placeholder customer, the other revenue benchmark, no label in tag.tsv
    "0000000001-26-000001\tConcentrationRiskPercentage1\tus-gaap/2025\t20251231\t4\tpure\t0xe\t0\t0.1200\t\t0\t3\t\t0\t0\t4",
    # dropped: geographic concentration
    "0000000001-26-000001\tConcentrationRiskPercentage1\tus-gaap/2025\t20251231\t4\tpure\t0xb\t0\t0.4000\t\t0\t3\t\t0\t0\t4",
    # dropped: accounts receivable benchmark
    "0000000001-26-000001\tConcentrationRiskPercentage1\tus-gaap/2025\t20251231\t4\tpure\t0xc\t0\t0.2000\t\t0\t3\t\t0\t0\t4",
    # dropped: customer revenue without a named customer axis
    "0000000001-26-000001\tConcentrationRiskPercentage1\tus-gaap/2025\t20251231\t4\tpure\t0xd\t0\t0.5000\t\t0\t2\t\t0\t0\t4",
    # kept but flagged: share of one business segment's revenue, not the company's
    "0000000001-26-000001\tConcentrationRiskPercentage1\tus-gaap/2025\t20251231\t4\tpure\t0x10\t0\t0.4200\t\t0\t4\t\t0\t0\t4",
    # dropped: another tag on the same dimensions
    "0000000001-26-000001\tRevenues\tus-gaap/2025\t20251231\t4\tUSD\t0xa\t0\t1000\t\t0\t3\t\t0\t0\t-3",
]
DIM = [
    "dimhash\tsegments\tsegt",
    f"0xa\t{CUSTOMER_REVENUE}\t0",
    "0xb\tConcentrationRiskByBenchmark=SalesRevenueNet;"
    "ConcentrationRiskByType=GeographicConcentrationRisk;Geographical=FL;\t0",
    "0xc\tConcentrationRiskByBenchmark=AccountsReceivable;"
    "ConcentrationRiskByType=CustomerConcentrationRisk;MajorCustomers=WalMart;\t0",
    "0xd\tConcentrationRiskByBenchmark=SalesRevenueNet;"
    "ConcentrationRiskByType=CustomerConcentrationRisk;\t0",
    "0xe\tConcentrationRiskByBenchmark=RevenueFromContractWithCustomer;"
    "ConcentrationRiskByType=CustomerConcentrationRisk;MajorCustomers=CustomerA;\t0",
    "0x10\tBusinessSegments=Retail;ConcentrationRiskByBenchmark=SalesRevenueNet;"
    "ConcentrationRiskByType=CustomerConcentrationRisk;MajorCustomers=WalMart;\t0",
    # Real dim.tsv rows quote segments that contain a tab.
    '0xf\t"InvestmentIdentifier=Unfunded equity\tsecurities;"\t0',
]
SUB = [
    "adsh\tcik\tname\tform\tperiod\tfiled\tcountryba",
    "0000000001-26-000001\t1278027\tB&G FOODS, INC.\t10-K\t20251231\t20260303\tUS",
]
TAG = [
    "tag\tversion\tcustom\tabstract\tdatatype\tiord\tcrdr\ttlabel\tdoc",
    "WalMartMember\t0000000001-26-000001\t1\t1\tmember\t\t\t"
    "Walmart Inc. [Member]\tWalmart.",
]


def write_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, lines in (("num", NUM), ("dim", DIM), ("sub", SUB), ("tag", TAG)):
            zf.writestr(f"{name}.tsv", "\n".join(lines) + "\n")
    return path


def test_keeps_named_customer_revenue_facts(tmp_path: Path) -> None:
    facts = concentration_facts(write_zip(tmp_path / "2026_03_notes.zip"))
    assert facts.sort("revenue_share", descending=True).select(
        "cik",
        "filer_name",
        "customer_member",
        "member_label",
        "revenue_share",
        "total_share",
    ).rows() == [
        ("0001278027", "B&G FOODS, INC.", "WalMart", "Walmart Inc.", 0.42, False),
        ("0001278027", "B&G FOODS, INC.", "WalMart", "Walmart Inc.", 0.31, True),
        ("0001278027", "B&G FOODS, INC.", "CustomerA", "CustomerA", 0.12, True),
    ]
    row = facts.filter(pl.col("revenue_share") == 0.31).row(0, named=True)
    assert row["benchmark"] == "SalesRevenueNet"
    assert row["form"] == "10-K"
    assert row["ddate"] == "20251231"
    assert row["adsh"] == "0000000001-26-000001"


@pytest.fixture
def zip_client(tmp_path: Path) -> SecClient:
    body = write_zip(tmp_path / "source.zip").read_bytes()

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/2026_03_notes.zip")
        return httpx.Response(200, content=body)

    return SecClient(
        "Test User test@example.com",
        tmp_path / "cache",
        transport=httpx.MockTransport(respond),
    )


def test_load_month_writes_parquet_and_deletes_zip(
    zip_client: SecClient, tmp_path: Path
) -> None:
    raw, processed = tmp_path / "raw", tmp_path / "processed"
    out = load_month(zip_client, "2026_03", raw_dir=raw, out_dir=processed)
    assert out == processed / "concentration_2026_03.parquet"
    assert pl.read_parquet(out).height == 3
    assert list(raw.iterdir()) == []
