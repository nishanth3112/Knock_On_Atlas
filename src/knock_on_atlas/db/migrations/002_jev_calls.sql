-- One row per Jev request: spend (input tokens) and the exact payload for reruns.
CREATE TABLE jev_calls (
  id serial PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  purpose text NOT NULL,
  route text NOT NULL,
  model text NOT NULL,
  request_id text,
  input_tokens int NOT NULL,
  output_tokens int NOT NULL,
  latency_ms int NOT NULL,
  request jsonb NOT NULL,
  answers jsonb NOT NULL
);
