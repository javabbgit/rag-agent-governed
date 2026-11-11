"""Retrieval sanity smoke: 12 queries x bm25, vector, hybrid. Needs the DB and the embedder.

Writes reports/retrieval/smoke_<UTC>.json (chunk ids and ranks only: no text, vectors or keys).
Exit 1 on any error; otherwise 0 whatever the hit counts (the human judges them).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rag_agent.config import ConfigError, Settings, load_settings
from rag_agent.ingest import QUERY_PREFIX, git_info
from rag_agent.llm_client import LLMClient
from rag_agent.retrieve import MODES, retrieve

REPO_ROOT = Path(__file__).resolve().parent.parent

# sanity only - never part of the D4 test set
SMOKE_QUERIES: list[tuple[str, str, list[str]]] = [
    ("Q01", "What is the maximum TDS ratio?", ["POL-003"]),
    (
        "Q02",
        "How much can I prepay as a lump sum on an FX5-C mortgage?",
        ["POL-009", "PRD-001", "FAQ-002"],
    ),
    ("Q03", "What fields does form EXC-100 require?", ["PRC-005"]),
    ("Q04", "How much is the discharge fee?", ["RATE-002"]),
    ("Q05", "How long can a rate be held for a refinance?", ["RATE-003"]),
    ("Q06", "Can I pay extra on my mortgage?", ["FAQ-002", "POL-009"]),
    (
        "Q07",
        "What happens if I give no instructions when my mortgage matures?",
        ["PRC-007", "FAQ-003", "PRD-004"],
    ),
    ("Q08", "Who can approve a loan above 750,000?", ["PRC-004"]),
    (
        "Q09",
        "How is income calculated for someone who runs their own business?",
        ["POL-008", "PRD-007"],
    ),
    (
        "Q10",
        "What is the maximum combined loan-to-value on a home equity line?",
        ["PRD-005"],
    ),
    ("Q11", "What is the stress test rate for mortgage approval?", ["POL-004"]),
    (
        "Q12",
        "How long does the lender have to give a final response to a complaint?",
        ["PRC-008"],
    ),
]


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def first_expected_rank(doc_ids: list[str], expected: list[str]) -> int | None:
    """1-based rank of the first hit whose doc_id is expected, else None."""
    for i, d in enumerate(doc_ids, start=1):
        if d in expected:
            return i
    return None


def fingerprint(tops: dict[str, dict[str, list[str]]]) -> str:
    return hashlib.sha256(json.dumps(tops, sort_keys=True).encode("utf-8")).hexdigest()


def run_smoke(
    settings: Settings,
    collection: str = "main",
    *,
    client: Any = None,
    retrieve_fn: Any = retrieve,
    reports_dir: Path | None = None,
) -> int:
    reports_dir = reports_dir or REPO_ROOT / "reports" / "retrieval"
    started = datetime.now(UTC)
    client = client or LLMClient(settings)
    tops: dict[str, dict[str, list[str]]] = {}
    per_query: dict[str, dict[str, Any]] = {}
    hits5 = dict.fromkeys(MODES, 0)
    n_errors = 0

    for qid, query, expected in SMOKE_QUERIES:
        tops[qid] = {}
        per_query[qid] = {}
        try:
            vec = client.embed([QUERY_PREFIX + query]).vectors[0]
        except Exception as exc:
            print(f"[FAIL] {qid} embed: {type(exc).__name__}")
            n_errors += 1
            continue
        cells = []
        for mode in MODES:
            try:
                hits = retrieve_fn(
                    query,
                    mode,
                    10,
                    collection,
                    True,
                    vec,
                    client=client,
                    dsn=settings.database_url,
                )
            except Exception as exc:
                print(f"[FAIL] {qid} {mode}: {type(exc).__name__}")
                n_errors += 1
                continue
            doc_ids = [h.doc_id for h in hits]
            first = first_expected_rank(doc_ids, expected)
            hit5 = first is not None and first <= 5
            hits5[mode] += hit5
            tops[qid][mode] = [h.chunk_id for h in hits]
            per_query[qid][mode] = {"top10": tops[qid][mode], "first_expected_rank": first}
            cells.append(f"{mode} {'hit' if hit5 else 'miss'} {first or '-'}")
        print(f"{qid}  " + "  |  ".join(cells))

    print(f"bm25 {hits5['bm25']}/12 · vector {hits5['vector']}/12 · hybrid {hits5['hybrid']}/12")
    finished = datetime.now(UTC)
    commit, dirty = git_info()
    report = {
        "started_utc": _iso(started),
        "finished_utc": _iso(finished),
        "mode": settings.llm_mode,
        "EMBED_MODEL": settings.embed_model,
        "git_commit": commit,
        "git_dirty": dirty,
        "collection": collection,
        "queries": per_query,
        "hits_at_5": hits5,
        "fingerprint": fingerprint(tops),
        "n_errors": n_errors,
    }
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    (reports_dir / f"smoke_{stamp}.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return 1 if n_errors else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Retrieval sanity smoke (12 queries, 3 modes).")
    ap.add_argument("--env-file", help="path to an env file (default: ./.env if present)")
    ap.add_argument("--collection", default="main")
    args = ap.parse_args(argv)
    try:
        settings = load_settings(args.env_file)
    except ConfigError as exc:
        print(f"[FAIL] config: {exc}")
        return 2
    return run_smoke(settings, args.collection)


if __name__ == "__main__":
    sys.exit(main())
