"""Idempotent ingestion of the corpus into pgvector.

Embeds only chunks whose content hash changed, one chunk per request, sequentially, through
LLMClient.embed (the single model entry point). Nothing is written to documents/chunks unless
every embedding succeeded; then everything is written in one transaction.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rag_agent import store as default_store
from rag_agent.config import ConfigError, Settings, load_settings
from rag_agent.corpus import parse_doc
from rag_agent.llm_client import LLMClient

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "
REQUIRED_DIM = 768
PAUSE_SECONDS = 0.2


class InvalidVectorError(ValueError):
    """An embedding is not 768 finite floats."""


@dataclass(frozen=True)
class Prepared:
    """A chunk ready to embed. Holds text, so it never goes into a report."""

    row: default_store.ChunkRow  # embedding is empty until embedded


def header(doc_id: str, title: str, status: str) -> str:
    return f"{doc_id} {title}" + (" (superseded)" if status == "superseded" else "")


def index_text(doc_id: str, title: str, status: str, section_text: str) -> str:
    return header(doc_id, title, status) + "\n" + section_text


def embed_input(text: str) -> str:
    return DOC_PREFIX + text


def content_sha256(embed_in: str, embed_model: str, embed_dim: int) -> str:
    payload = embed_in + "\n" + embed_model + "\n" + str(embed_dim)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_vector(vec: list[float], dim: int = REQUIRED_DIM) -> list[float]:
    if len(vec) != dim:
        raise InvalidVectorError(f"vector length {len(vec)} != {dim}")
    if not all(isinstance(x, int | float) and math.isfinite(x) for x in vec):
        raise InvalidVectorError("vector has non-finite values")
    return vec


def utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def git_info() -> tuple[str | None, bool | None]:
    def run(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    commit = run("rev-parse", "HEAD")
    status = run("status", "--porcelain")
    return commit, (None if status is None else bool(status))


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def build_corpus(
    docs_dir: Path, embed_model: str, embed_dim: int
) -> tuple[list[default_store.DocumentRow], list[Prepared]]:
    """Parse DOCS_DIR into document rows and chunk rows (embedding empty), in corpus order."""
    documents: list[default_store.DocumentRow] = []
    chunks: list[Prepared] = []
    for path in sorted(docs_dir.glob("*.md"), key=lambda p: p.stem):
        doc = parse_doc(path)
        fm = doc.front_matter
        doc_id = str(fm.get("id", path.stem))
        title = str(fm.get("title") or doc.title or doc_id)
        status = str(fm["status"])
        documents.append(
            default_store.DocumentRow(
                doc_id=doc_id,
                title=title,
                doc_type=str(fm["doc_type"]),
                status=status,
                version=str(fm["version"]),
                effective_date=fm["effective_date"],
                supersedes=fm.get("supersedes"),
                superseded_by=fm.get("superseded_by"),
                source_path=_rel(path),
                source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        )
        for position, sec in enumerate(doc.sections):
            itext = index_text(doc_id, title, status, sec.text)
            chunks.append(
                Prepared(
                    default_store.ChunkRow(
                        chunk_id=sec.chunk_id,
                        doc_id=doc_id,
                        position=position,
                        heading=sec.heading,
                        body=sec.text,
                        index_text=itext,
                        word_count=sec.word_count,
                        content_sha256=content_sha256(embed_input(itext), embed_model, embed_dim),
                        embed_model=embed_model,
                        embedding=[],
                    )
                )
            )
    return documents, chunks


def _write_report(reports_dir: Path, report: dict[str, Any]) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = reports_dir / f"ingest_{stamp}.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return path


def run_ingest(
    settings: Settings,
    collection: str = "main",
    docs_dir: str | Path | None = None,
    rebuild: bool = False,
    *,
    client: Any = None,
    store: Any = default_store,
    sleep: Callable[[float], None] = time.sleep,
    reports_dir: Path | None = None,
    chunks_csv: Path | None = None,
) -> int:
    """Run one ingest. Returns the exit code: 0 ok, 1 failed, 2 configuration error."""
    s = settings
    docs_path = Path(docs_dir) if docs_dir else REPO_ROOT / "corpus" / "docs"
    reports_dir = reports_dir or REPO_ROOT / "reports" / "ingest"
    chunks_csv = chunks_csv or REPO_ROOT / "reports" / "corpus" / "chunks.csv"

    if s.embed_dim != REQUIRED_DIM:
        print(f"[FAIL] config: EMBED_DIM must be {REQUIRED_DIM}, got {s.embed_dim}")
        return 2
    if s.llm_mode == "gateway" and not s.effective_embed_api_key:
        print("[FAIL] config: gateway mode needs an API key (OPENAI_API_KEY or EMBED_API_KEY)")
        return 2

    documents, prepared = build_corpus(docs_path, s.embed_model, s.embed_dim)
    corpus_ids = {p.row.chunk_id for p in prepared}
    if not corpus_ids:
        print(f"[FAIL] no chunks found in {docs_path}")
        return 1
    if docs_path.resolve() == (REPO_ROOT / "corpus" / "docs").resolve() and chunks_csv.is_file():
        with chunks_csv.open(encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))[1:]
        frozen = {r[0] for r in rows if r}
        if frozen != corpus_ids:
            print(
                f"[FAIL] chunk ids differ from {_rel(chunks_csv)}: "
                f"{len(corpus_ids - frozen)} new, {len(frozen - corpus_ids)} missing"
            )
            return 1

    started = utc_now()
    results: list[dict[str, Any]] = []
    vectors: dict[str, list[float]] = {}
    deleted_ids: list[str] = []
    n_skipped = 0
    n_errors = 0
    db_error: str | None = None
    to_embed = prepared

    try:
        stored = {} if rebuild else store.get_chunk_hashes(collection, dsn=s.database_url)
        to_embed = [p for p in prepared if stored.get(p.row.chunk_id) != p.row.content_sha256]
        n_skipped = len(prepared) - len(to_embed)

        if client is None and to_embed:
            client = LLMClient(s)
        for i, p in enumerate(to_embed):
            if i:
                sleep(PAUSE_SECONDS)
            sent = utc_now()
            t0 = time.perf_counter()
            error_class = None
            try:
                res = client.embed([embed_input(p.row.index_text)])
                if len(res.vectors) != 1:
                    raise InvalidVectorError(f"expected 1 vector, got {len(res.vectors)}")
                vectors[p.row.chunk_id] = validate_vector(list(res.vectors[0]), s.embed_dim)
            except Exception as exc:  # any error stops the run; never retried
                error_class = type(exc).__name__
            results.append(
                {
                    "chunk_id": p.row.chunk_id,
                    "sent_utc": _iso(sent),
                    "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "ok": error_class is None,
                    "error_class": error_class,
                }
            )
            if error_class:
                n_errors += 1
                break

        if not n_errors:
            rows = [replace(p.row, embedding=vectors[p.row.chunk_id]) for p in to_embed]
            embedded_ids = {p.row.chunk_id for p in to_embed}
            skipped_rows = [p.row for p in prepared if p.row.chunk_id not in embedded_ids]
            deleted_ids = store.write_run(
                collection,
                documents,
                rows,
                corpus_ids,
                dsn=s.database_url,
                skipped_chunks=skipped_rows,
            )
    except Exception as exc:
        db_error = type(exc).__name__
        n_errors += 1

    ok = n_errors == 0
    n_embedded = len(to_embed) if ok else 0
    n_deleted = len(deleted_ids)
    commit, dirty = git_info()
    finished = utc_now()

    try:
        store.record_run(
            collection,
            started_utc=started,
            finished_utc=finished,
            mode=s.llm_mode,
            embed_model=s.embed_model,
            embed_dim=s.embed_dim,
            git_commit=commit or "unknown",
            git_dirty=True if dirty is None else dirty,
            n_docs=len(documents),
            n_chunks=len(prepared),
            n_embedded=n_embedded,
            n_skipped=n_skipped,
            n_deleted=n_deleted,
            ok=ok,
            dsn=s.database_url,
        )
    except Exception as exc:
        db_error = db_error or f"record_run:{type(exc).__name__}"
        n_errors += 1
        ok = False

    report = {
        "started_utc": _iso(started),
        "finished_utc": _iso(finished),
        "mode": s.llm_mode,
        "embed_base_url_host": urlparse(s.effective_embed_base_url).hostname,
        "embed_model": s.embed_model,
        "embed_dim": s.embed_dim,
        "collection": collection,
        "docs_dir": _rel(docs_path),
        "git_commit": commit,
        "git_dirty": dirty,
        "n_docs": len(documents),
        "n_chunks": len(prepared),
        "n_embedded": n_embedded,
        "n_skipped": n_skipped,
        "n_deleted": n_deleted,
        "deleted_ids": deleted_ids,
        "n_errors": n_errors,
        "db_error_class": db_error,
        "ok": ok,
        "chunks": results,
    }
    _write_report(reports_dir, report)
    for r in results:
        if not r["ok"]:
            print(f"[FAIL] {r['chunk_id']}: {r['error_class']}")
    if db_error:
        print(f"[FAIL] database: {db_error}")
    print(
        f"ingest {collection}: docs {len(documents)}, chunks {len(prepared)}, "
        f"embedded {n_embedded}, skipped {n_skipped}, deleted {n_deleted}, "
        f"errors {n_errors}, {'ok' if ok else 'FAILED'}"
    )
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ingest the corpus into pgvector (idempotent).")
    ap.add_argument("--env-file", help="path to an env file (default: ./.env if present)")
    ap.add_argument("--collection", default="main")
    ap.add_argument("--docs-dir", default=str(REPO_ROOT / "corpus" / "docs"))
    ap.add_argument("--rebuild", action="store_true", help="re-embed every chunk")
    args = ap.parse_args(argv)
    try:
        s = load_settings(args.env_file)
    except ConfigError as exc:
        print(f"[FAIL] config: {exc}")
        return 2
    return run_ingest(s, args.collection, args.docs_dir, args.rebuild)


if __name__ == "__main__":
    sys.exit(main())
