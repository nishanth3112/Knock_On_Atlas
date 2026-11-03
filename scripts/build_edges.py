"""Load FS&N concentration facts into Postgres and build supplier -> hub edges.

Calls Jev for fuzzy labels and relationship checks unless JEV_ROUTE=off, then
writes counts to evals/edge_load/<date>.json.

Usage: uv run python scripts/build_edges.py
"""

import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import psycopg

from knock_on_atlas.config import get_settings
from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.edgar.submissions import company_profile
from knock_on_atlas.graph_store.edges import (
    Edge,
    candidates,
    edge_status,
    fact_rows,
    insert_facts,
    next_annual_filing,
    one_edge_per_hub,
    real_suppliers_by_hub,
    replace_edges,
    sic_flag,
    upsert_companies,
)
from knock_on_atlas.graph_store.resolve import MatchKind, load_hubs, resolve
from knock_on_atlas.jev.client import JevCall, jev_from_settings, record_call
from knock_on_atlas.jev.questions import (
    EntityDecision,
    choose_entity,
    relationship_flag,
    relationship_state,
)

FSN_DIR = Path("data/processed/fsn")
HUBS = Path("data/hubs.csv")
SUMMARY_DIR = Path("evals/edge_load")

settings = get_settings()
sec = SecClient(settings.sec_user_agent, Path("data/raw/sec_cache"))
jev = jev_from_settings(settings)
hubs = load_hubs(HUBS)
rows = fact_rows(
    pl.concat(pl.read_parquet(p) for p in sorted(FSN_DIR.glob("*.parquet")))
)
picked = candidates(rows)
resolved = {label: resolve(label, hubs) for label in picked["member_label"].unique()}
tokens = 0

with psycopg.connect(settings.database_url.get_secret_value(), autocommit=True) as conn:

    def log(call: JevCall | None, purpose: str) -> None:
        global tokens
        if call is not None:
            record_call(conn, call, purpose)
            tokens += call.response.usage.input_tokens

    new_facts = insert_facts(conn, rows)
    matchable = picked.filter(
        pl.col("member_label").map_elements(
            lambda label: (
                resolved[label].kind in (MatchKind.ALIAS_EXACT, MatchKind.FUZZY)
            ),
            return_dtype=pl.Boolean,
        )
    )
    profiles = {
        cik: company_profile(sec, cik)
        for cik in sorted(set(matchable["cik"]) | {hub.cik for hub in hubs})
    }

    edges: list[Edge] = []
    for row in matchable.iter_rows(named=True):
        supplier = profiles[row["cik"]]
        resolution = resolved[row["member_label"]]
        if resolution.kind == MatchKind.ALIAS_EXACT:
            decision = EntityDecision(resolution.candidates[0].hub, 1.0, "alias_exact")
        else:
            decision, call = choose_entity(
                jev, row["member_label"], supplier.name, list(resolution.candidates)
            )
            log(call, "j3")
        hub = decision.hub
        if hub is None:
            continue
        flag = sic_flag(supplier.sic, hub.cik)
        if flag is None:
            state = relationship_state(
                supplier.name, supplier.sic_description, hub.name, row["member_label"]
            )
            flag, call = relationship_flag(jev, state)
            log(call, "relationship")
        edges.append(
            Edge(
                src_cik=supplier.cik,
                dst_cik=hub.cik,
                pct=row["pct"],
                period_end=row["period_end"],
                method="xbrl",
                extraction_method=decision.method,
                member_label=row["member_label"],
                total_share=row["total_share"],
                relationship_flag=flag,
                source_accession=row["accession"],
                confidence=decision.confidence,
                status=edge_status(decision.confidence, row["total_share"], flag),
                valid_from=row["filed"],
                valid_to=next_annual_filing(row["filed"], supplier.ten_k_filed),
            )
        )

    edges, same_hub_dropped = one_edge_per_hub(edges)
    upsert_companies(conn, list(profiles.values()), hubs)
    inserted = replace_edges(conn, edges)
    real_suppliers = real_suppliers_by_hub(conn)
    # What is stored, including reviewer decisions the rebuild kept.
    stored = conn.execute(
        "SELECT count(*) FILTER (WHERE status = 'approved'),"
        " count(*) FILTER (WHERE status = 'pending'),"
        " count(*) FILTER (WHERE status = 'rejected'),"
        " count(*) FILTER (WHERE status = 'pending' AND NOT total_share),"
        " count(*) FILTER (WHERE reviewed_by IS NOT NULL),"
        " count(DISTINCT src_cik) FILTER (WHERE status = 'approved'),"
        " count(DISTINCT src_cik)"
        " FROM entity_edges"
    ).fetchone()

summary: dict[str, Any] = {
    "date": date.today().isoformat(),
    "jev_route": settings.jev_route.value,
    "facts": {"read": len(rows), "new": new_facts},
    "candidates": {"rows": len(picked), "suppliers": picked["cik"].n_unique()},
    "resolution_by_label": dict(Counter(r.kind.value for r in resolved.values())),
    "resolution_by_candidate": dict(
        Counter(resolved[label].kind.value for label in picked["member_label"])
    ),
    "fuzzy_near_misses": sum(r.kind == MatchKind.NEAR_MISS for r in resolved.values()),
    "jev_input_tokens": tokens,
    "edges": {
        "built": len(edges),
        "same_hub_labels_dropped": same_hub_dropped,
        "inserted": inserted,
        "by_extraction_method": dict(Counter(e.extraction_method for e in edges)),
        "by_relationship_flag": dict(Counter(e.relationship_flag for e in edges)),
        "open_windows": sum(e.valid_to is None for e in edges),
    },
    "stored": dict(
        zip(
            [
                "approved",
                "pending",
                "rejected",
                "pending_partial_share",
                "reviewed",
                "suppliers_with_approved_edges",
                "suppliers",
            ],
            stored or (),
            strict=True,
        )
    ),
    # Hub rule: a hub stays only with >= 2 of these.
    "real_suppliers_by_hub": {
        f"{hub.cik} {hub.name}": real_suppliers.get(hub.cik, 0) for hub in hubs
    },
}
SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
out = SUMMARY_DIR / f"{summary['date']}.json"
out.write_text(json.dumps(summary, indent=2, default=str) + "\n")
print(json.dumps(summary, indent=2, default=str))
print(out)
