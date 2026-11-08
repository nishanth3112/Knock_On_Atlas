-- One row per analysis run (spec §7.2); the id is also the run's checkpoint thread.
CREATE TABLE analyses (
  id uuid PRIMARY KEY,
  user_id int,
  type text NOT NULL DEFAULT 'event',
  input jsonb NOT NULL,
  status text NOT NULL
    CHECK (status IN ('queued', 'paused', 'published', 'rejected', 'ended')),
  result jsonb,
  risk_level text,
  confidence real,
  trace_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz
);
