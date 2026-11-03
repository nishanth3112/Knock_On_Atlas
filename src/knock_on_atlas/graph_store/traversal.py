from dataclasses import dataclass
from datetime import date

import psycopg

MAX_DEPTH = 2

# Suppliers exposed to a hub, directly or through one intermediate supplier, using
# only edges approved and valid on as_of. `nodes` stops a path from revisiting a
# company, so a hub never shows up as exposed to itself.
EXPOSURE_SQL = """
WITH RECURSIVE exposure AS (
  SELECT e.src_cik AS supplier, e.dst_cik AS via, e.pct AS weight, 1 AS depth,
         ARRAY[e.id] AS path, ARRAY[e.dst_cik, e.src_cik] AS nodes
  FROM entity_edges e
  WHERE e.type = 'SUPPLIES_TO' AND e.status = 'approved' AND e.dst_cik = %(hub)s
    AND e.valid_from <= %(as_of)s AND (e.valid_to IS NULL OR e.valid_to > %(as_of)s)
  UNION ALL
  SELECT e.src_cik, x.supplier, x.weight * e.pct / 100.0, x.depth + 1,
         x.path || e.id, x.nodes || e.src_cik
  FROM entity_edges e JOIN exposure x ON e.dst_cik = x.supplier
  WHERE e.type = 'SUPPLIES_TO' AND e.status = 'approved' AND x.depth < %(max_depth)s
    AND e.valid_from <= %(as_of)s AND (e.valid_to IS NULL OR e.valid_to > %(as_of)s)
    AND NOT e.src_cik = ANY(x.nodes)
)
SELECT supplier, via, weight, depth, path FROM exposure
ORDER BY depth, weight DESC, supplier, path
"""


@dataclass(frozen=True)
class Exposure:
    supplier_cik: str
    via_cik: str
    # Percent of the via company's revenue at depth 1; at depth 2 a product of two
    # shares, a heuristic rather than a measured exposure.
    weight: float
    depth: int
    edge_ids: tuple[int, ...]


def exposure(conn: psycopg.Connection, hub_cik: str, as_of: date) -> list[Exposure]:
    rows = conn.execute(
        EXPOSURE_SQL, {"hub": hub_cik, "as_of": as_of, "max_depth": MAX_DEPTH}
    ).fetchall()
    return [
        Exposure(supplier, via, float(weight), depth, tuple(path))
        for supplier, via, weight, depth, path in rows
    ]
