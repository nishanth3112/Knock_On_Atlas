"""Rank named customers in loaded FS&N months by how many suppliers name them.

Usage: uv run python scripts/rank_customers.py [top_n]
"""

import sys
from pathlib import Path

import polars as pl

from knock_on_atlas.edgar.fsn import PLACEHOLDER_MEMBER

top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
facts = pl.concat(
    pl.read_parquet(p) for p in sorted(Path("data/processed/fsn").glob("*.parquet"))
)
ranking = (
    facts.filter(~pl.col("customer_member").str.contains(PLACEHOLDER_MEMBER))
    .with_columns(
        key=pl.col("member_label")
        .str.to_lowercase()
        .str.replace_all(r"[^a-z0-9]", "")
        .str.replace(
            r"(member|inc|corp|corporation|company|ltd|llc|plc|stores|com)+$", ""
        )
    )
    .group_by("key")
    .agg(
        suppliers=pl.col("cik").n_unique(),
        labels=pl.col("member_label").unique().sort().str.join(" | "),
    )
    .sort(["suppliers", "key"], descending=[True, False])
    .head(top_n)
)
with pl.Config(tbl_rows=top_n, fmt_str_lengths=80, tbl_width_chars=140):
    print(ranking)
