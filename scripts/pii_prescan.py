"""Send every corpus chunk once through the gateway's embedding path to see whether PII rules fire.

Gateway mode only. Calls LLMClient.embed (the single model entry point), one chunk per request,
sequentially. The report never contains chunk text, vectors or keys. Exit 1 on any error
(a policy block counts as an error).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rag_agent.config import ConfigError, Settings, load_settings
from rag_agent.corpus import Section, iter_chunks
from rag_agent.llm_client import LLMClient
from smoke import REPO_ROOT, git_info

PAUSE_SECONDS = 0.2


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def prescan(
    client: Any,
    chunks: Iterable[Section],
    pause: float = PAUSE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Embed each chunk alone. Record only ids, timings and the error class name."""
    results: list[dict[str, Any]] = []
    for i, chunk in enumerate(chunks):
        if i:
            sleep(pause)
        sent = utc_now()
        started = time.perf_counter()
        ok, error_class = True, None
        try:
            client.embed([chunk.text])
        except Exception as exc:  # any failure is an error; never retried
            ok, error_class = False, type(exc).__name__
        results.append(
            {
                "chunk_id": chunk.chunk_id,
                "sent_utc": sent,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "ok": ok,
                "error_class": error_class,
            }
        )
    return results


def build_report(
    s: Settings, start: str, end: str, results: list[dict[str, Any]]
) -> dict[str, Any]:
    commit, dirty = git_info()
    return {
        "start_utc": start,
        "end_utc": end,
        "mode": s.llm_mode,
        "embed_base_url": s.effective_embed_base_url,
        "embed_model": s.embed_model,
        "git_commit": commit,
        "git_dirty": dirty,
        "n_chunks": len(results),
        "n_errors": sum(not r["ok"] for r in results),
        "chunks": results,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env-file", help="path to an env file (default: ./.env if present)")
    ap.add_argument("--doc", help="only scan chunks of this document id")
    ap.add_argument("--docs-dir", default=str(REPO_ROOT / "corpus" / "docs"))
    ap.add_argument("--out-dir", default=str(REPO_ROOT / "reports" / "corpus"))
    args = ap.parse_args(argv)

    try:
        s = load_settings(args.env_file)
    except ConfigError as exc:
        print(f"[FAIL] config: {exc}")
        return 1
    if s.llm_mode != "gateway":
        print("[FAIL] pii_prescan only runs with LLM_MODE=gateway")
        return 1

    chunks = [c for c in iter_chunks(args.docs_dir) if args.doc in (None, c.doc_id)]
    if not chunks:
        print("[FAIL] no chunks found")
        return 1

    client = LLMClient(s)
    start = utc_now()
    results = prescan(client, chunks)
    report = build_report(s, start, utc_now(), results)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"prescan_{stamp}.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    for r in results:
        if not r["ok"]:
            print(f"[FAIL] {r['chunk_id']}: {r['error_class']}")
    print(f"chunks={report['n_chunks']} errors={report['n_errors']}")
    print(f"report: {path}")
    return 1 if report["n_errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
