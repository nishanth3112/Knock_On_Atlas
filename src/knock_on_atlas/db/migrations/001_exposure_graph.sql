CREATE TABLE companies (
  cik text PRIMARY KEY,
  name text NOT NULL,
  sic text,
  tickers text[] NOT NULL DEFAULT '{}',
  aliases text[] NOT NULL DEFAULT '{}',
  is_hub boolean NOT NULL DEFAULT false
);

-- One row per FS&N customer revenue-concentration fact, named or placeholder.
CREATE TABLE concentration_facts (
  id serial PRIMARY KEY,
  cik text NOT NULL,
  accession text NOT NULL,
  form text NOT NULL,
  filed date NOT NULL,
  fiscal_period_end date,
  period_end date NOT NULL,
  qtrs int NOT NULL,
  member text NOT NULL,
  member_label text NOT NULL,
  pct numeric,
  benchmark text NOT NULL,
  segments text NOT NULL,
  total_share boolean NOT NULL,
  is_named boolean NOT NULL,
  UNIQUE (accession, segments, period_end, qtrs)
);

CREATE TABLE entity_edges (
  id serial PRIMARY KEY,
  src_cik text NOT NULL,
  dst_cik text NOT NULL,
  type text NOT NULL DEFAULT 'SUPPLIES_TO',
  pct numeric,
  metric text NOT NULL DEFAULT 'revenue',
  period_end date NOT NULL,
  method text NOT NULL,
  extraction_method text NOT NULL,
  member_label text NOT NULL,
  total_share boolean NOT NULL,
  relationship_flag text,
  source_accession text NOT NULL,
  confidence real NOT NULL,
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'approved', 'rejected')),
  valid_from date NOT NULL,
  valid_to date,
  UNIQUE (src_cik, dst_cik, type, period_end, method)
);
CREATE INDEX ON entity_edges (dst_cik, type, status);
