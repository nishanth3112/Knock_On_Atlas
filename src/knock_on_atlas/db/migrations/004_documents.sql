-- One row per extracted filing section; the text itself stays in the SEC cache.
CREATE TABLE documents (
  id serial PRIMARY KEY,
  cik text NOT NULL,
  accession text NOT NULL,
  form text NOT NULL,
  section text NOT NULL,
  url text NOT NULL,
  published_at date NOT NULL,
  content_hash text NOT NULL,
  chars int NOT NULL,
  trust_level text NOT NULL DEFAULT 'external',
  UNIQUE (accession, section)
);
CREATE INDEX ON documents (cik, published_at);
