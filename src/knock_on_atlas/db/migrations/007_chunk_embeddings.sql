-- In public, so every schema (including test schemas) shares one vector type.
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
ALTER TABLE document_chunks ADD COLUMN embedding vector(384);
CREATE INDEX document_chunks_embedding_idx ON document_chunks
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
