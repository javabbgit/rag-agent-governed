CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS documents (
  collection text NOT NULL DEFAULT 'main', doc_id text NOT NULL, title text NOT NULL,
  doc_type text NOT NULL, status text NOT NULL, version text NOT NULL,
  effective_date date NOT NULL, supersedes text, superseded_by text,
  source_path text NOT NULL, source_sha256 text NOT NULL,
  PRIMARY KEY (collection, doc_id));
CREATE TABLE IF NOT EXISTS chunks (
  collection text NOT NULL DEFAULT 'main', chunk_id text NOT NULL, doc_id text NOT NULL,
  position int NOT NULL, heading text NOT NULL, body text NOT NULL, index_text text NOT NULL,
  word_count int NOT NULL, content_sha256 text NOT NULL, embed_model text NOT NULL,
  embedding vector(768) NOT NULL, ingested_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (collection, chunk_id),
  FOREIGN KEY (collection, doc_id) REFERENCES documents ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks (collection, doc_id);
CREATE TABLE IF NOT EXISTS ingest_runs (
  run_id bigserial PRIMARY KEY, started_utc timestamptz NOT NULL, finished_utc timestamptz,
  collection text NOT NULL, mode text NOT NULL, embed_model text NOT NULL, embed_dim int NOT NULL,
  git_commit text NOT NULL, git_dirty boolean NOT NULL,
  n_docs int, n_chunks int, n_embedded int, n_skipped int, n_deleted int,
  ok boolean NOT NULL DEFAULT false);
