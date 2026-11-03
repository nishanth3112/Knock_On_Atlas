-- cik and source_date are columns (not only metadata) because every search filters on them.
CREATE TABLE document_chunks (
  id serial PRIMARY KEY,
  document_id int NOT NULL REFERENCES documents ON DELETE CASCADE,
  ord int NOT NULL,
  cik text NOT NULL,
  source_date date NOT NULL,
  content text NOT NULL,
  metadata jsonb NOT NULL,
  UNIQUE (document_id, ord)
);
CREATE INDEX ON document_chunks (cik, source_date);
