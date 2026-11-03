-- The latest bm25s index as one zip archive (its files plus the chunk id of each row).
CREATE TABLE bm25_index (
  id int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  built_at timestamptz NOT NULL DEFAULT now(),
  chunks int NOT NULL,
  archive bytea NOT NULL
);
