"""Draft the eval events: each hub's material 8-Ks filed since SINCE.

Writes evals/cases/events.csv, a draft to curate into the eval suite by hand.

Usage: uv run python scripts/draft_eval_events.py
"""

import csv
from collections import Counter
from datetime import date
from pathlib import Path

from knock_on_atlas.config import get_settings
from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.edgar.submissions import material_8ks, recent_8ks
from knock_on_atlas.graph_store.resolve import load_hubs

# The first FS&N month the edges come from.
SINCE = date(2025, 11, 1)
OUT = Path("evals/cases/events.csv")

settings = get_settings()
sec = SecClient(settings.sec_user_agent, Path("data/raw/sec_cache"))
rows = [
    {
        "accession": f.accession,
        "hub_cik": hub.cik,
        "hub_name": hub.name,
        "accepted_at": f.accepted_at.isoformat(),
        "items": "|".join(f.items),
        "url": f.url,
    }
    for hub in load_hubs(Path("data/hubs.csv"))
    for f in material_8ks(recent_8ks(sec, hub.cik), SINCE)
]
rows.sort(key=lambda row: row["accepted_at"])
OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
print(f"{len(rows)} events from {len({r['hub_cik'] for r in rows})} hubs -> {OUT}")
print(dict(Counter(r["hub_name"] for r in rows)))
