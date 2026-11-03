"""Review supplier -> hub edges.

Usage:
  uv run python scripts/review_edges.py list [--status pending]
  uv run python scripts/review_edges.py approve ID [ID ...] [--by NAME]
  uv run python scripts/review_edges.py reject ID [ID ...] [--by NAME]
"""

import argparse
import getpass

import psycopg

from knock_on_atlas.config import get_settings
from knock_on_atlas.graph_store.review import list_edges, set_status

parser = argparse.ArgumentParser()
commands = parser.add_subparsers(dest="command", required=True)
listing = commands.add_parser("list")
listing.add_argument("--status", choices=["pending", "approved", "rejected"])
for name in ("approve", "reject"):
    decide = commands.add_parser(name)
    decide.add_argument("ids", nargs="+", type=int)
    decide.add_argument("--by", default=getpass.getuser())
args = parser.parse_args()

url = get_settings().database_url.get_secret_value()
with psycopg.connect(url, autocommit=True) as conn:
    if args.command == "list":
        for e in list_edges(conn, args.status):
            share = "" if e.total_share else " part-share"
            flag = f" [{e.relationship_flag}]" if e.relationship_flag else ""
            print(
                f"{e.id:>5} {e.status:<8} {e.supplier[:30]:<30} -> {e.hub[:24]:<24}"
                f" {e.pct:>6.2f}%{share}{flag} {e.extraction_method}"
                f" {e.confidence:.2f} '{e.member_label}' {e.filing_url}"
            )
    else:
        status = "approved" if args.command == "approve" else "rejected"
        done = set_status(conn, args.ids, status, args.by)
        print(f"{status}: {done}")
        if missing := sorted(set(args.ids) - set(done)):
            raise SystemExit(f"no such edge: {missing}")
