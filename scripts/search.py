"""Search CLI: make search Q="..." MODE=hybrid K=10. Logic lives in rag_agent.retrieve."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from typing import Any

from rag_agent.config import ConfigError, load_settings
from rag_agent.llm_client import LLMClient
from rag_agent.retrieve import DEFAULT_K, MODES, retrieve


def _fmt(v: Any) -> str:
    return "-" if v is None else str(v)


def main(argv: list[str] | None = None, *, retrieve_fn: Any = retrieve, client: Any = None) -> int:
    ap = argparse.ArgumentParser(description="Search the ingested chunks.")
    ap.add_argument("query")
    ap.add_argument("--mode", choices=MODES, default="hybrid")
    ap.add_argument("-k", type=int, default=DEFAULT_K)
    ap.add_argument("--collection", default="main")
    ap.add_argument("--current-only", action="store_true", help="exclude superseded documents")
    ap.add_argument("--json", action="store_true", help="print hits without text as JSON")
    ap.add_argument("--env-file", help="path to an env file (default: ./.env if present)")
    args = ap.parse_args(argv)
    try:
        settings = load_settings(args.env_file)
    except ConfigError as exc:
        print(f"[FAIL] config: {exc}")
        return 2
    try:
        if client is None and args.mode != "bm25":
            client = LLMClient(settings)
        hits = retrieve_fn(
            args.query,
            args.mode,
            args.k,
            args.collection,
            not args.current_only,
            client=client,
            dsn=settings.database_url,
        )
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}")
        return 1
    if args.json:
        rows = [{k: v for k, v in dataclasses.asdict(h).items() if k != "text"} for h in hits]
        print(json.dumps(rows, indent=2))
        return 0
    print("rank  chunk_id  status  score  bm25_rank  vector_rank  heading")
    for h in hits:
        print(
            f"{h.rank:>4}  {h.chunk_id}  {h.status}  {h.score:.4f}  "
            f"{_fmt(h.bm25_rank)}  {_fmt(h.vector_rank)}  {h.heading}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
