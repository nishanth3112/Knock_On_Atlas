-- API users (spec §7.2); emails are stored lowercase.
CREATE TABLE users (
  id serial PRIMARY KEY,
  email text NOT NULL UNIQUE,
  password_hash text NOT NULL,
  role text NOT NULL CHECK (role IN ('analyst', 'reviewer', 'admin')),
  created_at timestamptz NOT NULL DEFAULT now()
);

-- One reviewer decision per paused analysis; the unique key stops a second one.
CREATE TABLE human_reviews (
  id serial PRIMARY KEY,
  analysis_id uuid NOT NULL UNIQUE,
  reviewer_id int NOT NULL,
  decision text NOT NULL CHECK (decision IN ('approved', 'rejected')),
  diff jsonb,
  comment text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now()
);
