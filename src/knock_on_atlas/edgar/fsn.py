import tempfile
import zipfile
from pathlib import Path

import polars as pl

from knock_on_atlas.edgar.client import SecClient

FSN_URL = (
    "https://www.sec.gov/files/dera/data/financial-statement-notes-data-sets/"
    "{month}_notes.zip"
)
CONCENTRATION_TAG = "ConcentrationRiskPercentage1"
REVENUE_BENCHMARKS = ["SalesRevenueNet", "RevenueFromContractWithCustomer"]

# Anonymized members such as CustomerA, TopTenCustomers or OnePurchaser. Case-sensitive
# on purpose: single capitals only match as CamelCase words, never inside real names.
_COUNT = (
    r"(One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|First|Second|Third|Fourth"
    r"|Fifth|Single|Largest|Top|All|Other|Major|Significant|Additional|No|\d+|[A-Z])"
)
_ROLE = (
    r"(Customer|Client|Purchaser|Partner|Distributor|Vendor|Company|Counterpart(y|ies)"
    r"|Tenant|Payor|Licensee|Reseller|Wholesaler|Carrier|Account)s?"
)
PLACEHOLDER_MEMBER = rf"^({_COUNT}*{_ROLE}{_COUNT}*|(All)?Others?)$"


def load_month(client: SecClient, month: str, raw_dir: Path, out_dir: Path) -> Path:
    zip_path = client.download(
        FSN_URL.format(month=month), raw_dir / f"{month}_notes.zip"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"concentration_{month}.parquet"
    concentration_facts(zip_path).write_parquet(out)
    zip_path.unlink()
    return out


def _axis(name: str) -> pl.Expr:
    # segments look like "ConcentrationRiskByType=...;MajorCustomers=WalMart;"
    return pl.col("segments").str.extract(rf"(?:^|;){name}=([^;]+)", 1)


def concentration_facts(zip_path: Path) -> pl.DataFrame:
    """Customer revenue-concentration facts that name a customer member."""
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(zip_path) as zf:

        def scan(name: str) -> pl.LazyFrame:
            zf.extract(f"{name}.tsv", tmp)
            return pl.scan_csv(
                Path(tmp, f"{name}.tsv"), separator="\t", infer_schema=False
            )

        labels = (
            scan("tag")
            .filter(pl.col("custom") == "1")
            .select(member_tag="tag", adsh="version", tlabel="tlabel")
        )
        return (
            scan("num")
            .filter(pl.col("tag") == CONCENTRATION_TAG)
            .join(scan("dim"), left_on="dimh", right_on="dimhash")
            .with_columns(
                concentration_type=_axis("ConcentrationRiskByType"),
                benchmark=_axis("ConcentrationRiskByBenchmark"),
                customer_member=_axis("MajorCustomers"),
            )
            .filter(
                pl.col("concentration_type") == "CustomerConcentrationRisk",
                pl.col("benchmark").is_in(REVENUE_BENCHMARKS),
                pl.col("customer_member").is_not_null(),
            )
            .join(scan("sub"), on="adsh")
            .with_columns(member_tag=pl.col("customer_member") + "Member")
            .join(labels, on=["member_tag", "adsh"], how="left")
            .select(
                cik=pl.col("cik").str.zfill(10),
                filer_name="name",
                form="form",
                period="period",
                filed="filed",
                countryba="countryba",
                adsh="adsh",
                ddate="ddate",
                qtrs="qtrs",
                customer_member="customer_member",
                member_label=pl.col("tlabel")
                .str.strip_suffix(" [Member]")
                .fill_null(pl.col("customer_member")),
                # FS&N stores the share as a fraction: 0.31 means 31% of revenue.
                revenue_share=pl.col("value").cast(pl.Float64),
                benchmark="benchmark",
                segments="segments",
            )
            # A fact presented twice in one filing repeats in num.tsv with only iprx and
            # footnote differing; neither is kept, so the repeats are exact duplicates.
            .unique(maintain_order=True)
            .collect()
        )
