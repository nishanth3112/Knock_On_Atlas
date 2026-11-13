-- 8-K filings from watched hubs: the inbox. Loaded from the eval set now; the
-- poller adds new ones.
CREATE TABLE events (
  accession text PRIMARY KEY,
  cik text NOT NULL,
  accepted_at timestamptz NOT NULL,
  items text[] NOT NULL,
  url text NOT NULL
);
CREATE INDEX idx_events_accepted_at ON events (accepted_at DESC);
