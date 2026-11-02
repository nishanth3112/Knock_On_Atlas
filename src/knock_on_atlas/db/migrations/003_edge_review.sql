-- Set by the review CLI; a rebuild never touches a reviewed edge.
ALTER TABLE entity_edges ADD COLUMN reviewed_by text, ADD COLUMN reviewed_at timestamptz;
