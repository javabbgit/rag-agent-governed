"""Postgres storage (pgvector). The only module that talks to the database.

All SQL is parameterized. Vectors are passed as bound text parameters '[v1,...]' cast with
::vector, so no pgvector Python package is needed. No vector index: search is an exact scan.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import psycopg

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "db" / "schema.sql"


class StoreError(RuntimeError):
    """Configuration problem with the database connection."""


@dataclass(frozen=True)
class DocumentRow:
    doc_id: str
    title: str
    doc_type: str
    status: str
    version: str
    effective_date: date
    supersedes: str | None
    superseded_by: str | None
    source_path: str
    source_sha256: str


@dataclass(frozen=True)
class ChunkRow:
    chunk_id: str
    doc_id: str
    position: int
    heading: str
    body: str
    index_text: str
    word_count: int
    content_sha256: str
    embed_model: str
    embedding: list[float]


def connect(dsn: str | None = None) -> psycopg.Connection[Any]:
    """Connect with `dsn`, or DATABASE_URL from the environment."""
    url = dsn or os.environ.get("DATABASE_URL")
    if not url:
        raise StoreError("DATABASE_URL is not set (see .env.example)")
    return psycopg.connect(url, connect_timeout=10)


def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def init_schema(dsn: str | None = None) -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with connect(dsn) as conn:
        conn.execute(sql)  # static DDL file, no parameters


def get_chunk_hashes(collection: str, dsn: str | None = None) -> dict[str, str]:
    with connect(dsn) as conn:
        rows = conn.execute(
            "SELECT chunk_id, content_sha256 FROM chunks WHERE collection = %s", (collection,)
        ).fetchall()
    return {r[0]: r[1] for r in rows}


def write_run(
    collection: str,
    documents: list[DocumentRow],
    chunks: list[ChunkRow],
    keep_chunk_ids: set[str] | list[str],
    dsn: str | None = None,
    skipped_chunks: list[ChunkRow] | None = None,
) -> list[str]:
    """Upsert documents and chunks, delete the rest of the collection, in ONE transaction.

    `skipped_chunks` are unchanged chunks (same hash, no new embedding): only their non-vector
    columns (doc_id, position, heading, word_count) are refreshed.

    Returns the ids of the deleted chunks (sorted).
    """
    keep_ids = sorted(keep_chunk_ids)
    doc_ids = sorted(d.doc_id for d in documents)
    with connect(dsn) as conn, conn.transaction():
        for d in documents:
            conn.execute(
                "INSERT INTO documents (collection, doc_id, title, doc_type, status, version,"
                " effective_date, supersedes, superseded_by, source_path, source_sha256)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (collection, doc_id) DO UPDATE SET title = EXCLUDED.title,"
                " doc_type = EXCLUDED.doc_type, status = EXCLUDED.status,"
                " version = EXCLUDED.version, effective_date = EXCLUDED.effective_date,"
                " supersedes = EXCLUDED.supersedes, superseded_by = EXCLUDED.superseded_by,"
                " source_path = EXCLUDED.source_path, source_sha256 = EXCLUDED.source_sha256",
                (
                    collection,
                    d.doc_id,
                    d.title,
                    d.doc_type,
                    d.status,
                    d.version,
                    d.effective_date,
                    d.supersedes,
                    d.superseded_by,
                    d.source_path,
                    d.source_sha256,
                ),
            )
        for c in chunks:
            conn.execute(
                "INSERT INTO chunks (collection, chunk_id, doc_id, position, heading, body,"
                " index_text, word_count, content_sha256, embed_model, embedding, ingested_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, now())"
                " ON CONFLICT (collection, chunk_id) DO UPDATE SET doc_id = EXCLUDED.doc_id,"
                " position = EXCLUDED.position, heading = EXCLUDED.heading,"
                " body = EXCLUDED.body, index_text = EXCLUDED.index_text,"
                " word_count = EXCLUDED.word_count,"
                " content_sha256 = EXCLUDED.content_sha256,"
                " embed_model = EXCLUDED.embed_model, embedding = EXCLUDED.embedding,"
                " ingested_at = now()",
                (
                    collection,
                    c.chunk_id,
                    c.doc_id,
                    c.position,
                    c.heading,
                    c.body,
                    c.index_text,
                    c.word_count,
                    c.content_sha256,
                    c.embed_model,
                    vector_literal(c.embedding),
                ),
            )
        for c in skipped_chunks or []:
            conn.execute(
                "UPDATE chunks SET doc_id = %s, position = %s, heading = %s, word_count = %s"
                " WHERE collection = %s AND chunk_id = %s",
                (c.doc_id, c.position, c.heading, c.word_count, collection, c.chunk_id),
            )
        deleted = conn.execute(
            "DELETE FROM chunks WHERE collection = %s AND NOT (chunk_id = ANY(%s::text[]))"
            " RETURNING chunk_id",
            (collection, keep_ids),
        ).fetchall()
        conn.execute(
            "DELETE FROM documents WHERE collection = %s AND NOT (doc_id = ANY(%s::text[]))",
            (collection, doc_ids),
        )
    return sorted(r[0] for r in deleted)


def record_run(
    collection: str,
    *,
    started_utc: datetime,
    finished_utc: datetime,
    mode: str,
    embed_model: str,
    embed_dim: int,
    git_commit: str,
    git_dirty: bool,
    n_docs: int | None,
    n_chunks: int | None,
    n_embedded: int | None,
    n_skipped: int | None,
    n_deleted: int | None,
    ok: bool,
    dsn: str | None = None,
) -> None:
    """Insert one ingest_runs row in its own transaction."""
    with connect(dsn) as conn:
        conn.execute(
            "INSERT INTO ingest_runs (started_utc, finished_utc, collection, mode, embed_model,"
            " embed_dim, git_commit, git_dirty, n_docs, n_chunks, n_embedded, n_skipped,"
            " n_deleted, ok) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                started_utc,
                finished_utc,
                collection,
                mode,
                embed_model,
                embed_dim,
                git_commit,
                git_dirty,
                n_docs,
                n_chunks,
                n_embedded,
                n_skipped,
                n_deleted,
                ok,
            ),
        )
