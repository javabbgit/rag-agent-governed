"""Dev-only sweep of the rerank candidate size N over RERANK_GRID (make eval-rerank-sweep).

Per question: one embedding, hybrid top-50 once, one rerank call per N. Per N: cand_recall,
Hit@1/3/5, MRR@10 with bootstrap CIs, and latency p50/p95. Selection: drop N with p95 above
5000 ms, take the best MRR@10 m* of the rest, select the smallest N with MRR@10 >= m* - 0.01.
Writes reports/eval/rerank_sweep_dev_<UTC>.json and .md (chunk ids and numbers only). Never the
test split, and no ledger line. Needs the DB, the embedder and the reranker.
Latency is the wall time of one retrieve(mode="rerank") call (local fusion plus the reranker).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import check_evalset
from eval_retrieval import COLLECTION, INCLUDE_SUPERSEDED, K, _f, _iso, questions_sha256
from rag_agent import evalset, metrics
from rag_agent.config import ConfigError, Settings, load_settings
from rag_agent.ingest import git_info
from rag_agent.llm_client import LLMClient
from rag_agent.rerank import RerankError, check_pin, percentile, reranker_info
from rag_agent.retrieve import RERANK_GRID, embed_query, retrieve

REPO_ROOT = Path(__file__).resolve().parent.parent
P95_LIMIT_MS = 5000.0
MRR_TOLERANCE = 0.01
SCORE_TOLERANCE = 1e-5


def select_n(
    rows: list[dict[str, Any]],
    p95_limit_ms: float = P95_LIMIT_MS,
    tol: float = MRR_TOLERANCE,
    log: Any = print,
) -> int | None:
    """The smallest N within `tol` of the best MRR@10 among the N with p95 <= the limit."""
    kept = []
    for r in rows:
        if r["p95_ms"] > p95_limit_ms:
            log(f"drop N={r['n']}: p95 {r['p95_ms']:.0f} ms > {p95_limit_ms:.0f} ms")
        else:
            kept.append(r)
    if not kept:
        log("no eligible N")
        return None
    best = max(r["mrr10"] for r in kept)
    log(f"m* = {best:.4f} over N in {[r['n'] for r in kept]}")
    floor = best - tol
    for r in sorted(kept, key=lambda r: r["n"]):
        if r["mrr10"] >= floor:
            log(f"select N={r['n']}: mrr10 {r['mrr10']:.4f} >= m* - {tol} = {floor:.4f}")
            return int(r["n"])
    log("no eligible N")
    return None


def score_inconsistencies(
    scores: dict[tuple[str, str], dict[int, float]], tol: float = SCORE_TOLERANCE
) -> list[tuple[str, str, float]]:
    """(question, chunk, spread) where one chunk's score differs by more than `tol` across N."""
    out = []
    for (qid, cid), by_n in sorted(scores.items()):
        if len(by_n) > 1:
            spread = max(by_n.values()) - min(by_n.values())
            if spread > tol:
                out.append((qid, cid, spread))
    return out


def markdown(report: dict[str, Any]) -> str:
    out = [
        "# Rerank candidate-size sweep: dev",
        "",
        f"started {report['started_utc']}, finished {report['finished_utc']}, "
        f"commit {report['git_commit']}, dirty {report['git_dirty']}, "
        f"questions_sha256 {report['questions_sha256']}",
        "",
        f"reranker {report['reranker']['model_id']} sha {report['reranker']['model_sha']} "
        f"version {report['reranker']['version']}; n_answerable {report['n_answerable']}",
        "",
        "| N | cand_recall | Hit@1 | Hit@3 | Hit@5 | MRR@10 [95% CI] | p50 ms | p95 ms |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in report["rows"]:
        lo, hi = r["mrr10_ci"]
        out.append(
            f"| {r['n']} | {_f(r['cand_recall'])} | {_f(r['hit1'])} | {_f(r['hit3'])} | "
            f"{_f(r['hit5'])} | {_f(r['mrr10'])} [{_f(lo)}, {_f(hi)}] | "
            f"{r['p50_ms']:.0f} | {r['p95_ms']:.0f} |"
        )
    out += ["", f"selected N = {report['selected_n'] if report['selected_n'] else 'none'}"]
    if report["score_warnings"]:
        out += ["", f"{len(report['score_warnings'])} score inconsistencies across N (see JSON)"]
    return "\n".join(out) + "\n"


def run_sweep(
    settings: Settings,
    split: str = "dev",
    *,
    client: Any = None,
    questions_path: Path | None = None,
    docs_dir: Path | None = None,
    manifest_path: Path | None = None,
    reports_dir: Path | None = None,
) -> int:
    if split != "dev":
        print("[REFUSED] the sweep is dev-only; tuning is judged on the dev split")
        return 2
    questions_path = questions_path or check_evalset.DEFAULT_QUESTIONS
    docs_dir = docs_dir or REPO_ROOT / "corpus" / "docs"
    manifest_path = manifest_path or REPO_ROOT / "corpus" / "manifest.toml"
    reports_dir = reports_dir or REPO_ROOT / "reports" / "eval"

    result = check_evalset.run_check(questions_path, docs_dir, manifest_path, evalset.LEVEL_DEFAULT)
    if result.n_fail:
        check_evalset.print_result(result)
        print("[FAIL] eval set check failed; no request sent")
        return 1
    qs = evalset.split_questions(evalset.load(questions_path), split)
    if not qs:
        print("no questions")
        return 1

    grid = list(RERANK_GRID)
    depth = max(grid)
    try:
        info = reranker_info(settings.reranker_url, settings.rerank_timeout_s)
        check_pin(info, settings.reranker_model_rev)
        max_batch = int(info["max_client_batch_size"])
    except (RerankError, KeyError, TypeError, ValueError) as exc:
        print(f"[FAIL] reranker preflight: {exc}; no request sent")
        return 1
    if max(grid) > max_batch:
        print(
            f"[FAIL] reranker preflight: N {max(grid)} > max_client_batch_size "
            f"{max_batch}; no request sent"
        )
        return 1
    started = datetime.now(UTC)
    n_ans = sum(q["kind"] == "answerable" for q in qs)
    cand_ranks: list[int | None] = []
    ranks: dict[int, list[int | None]] = {n: [] for n in grid}
    latency: dict[int, list[float]] = {n: [] for n in grid}
    scores: dict[tuple[str, str], dict[int, float]] = {}
    try:
        for q in qs:
            q_vec = embed_query(q["query"], client=client)
            common = {
                "collection": COLLECTION,
                "include_superseded": INCLUDE_SUPERSEDED,
                "client": client,
                "dsn": settings.database_url,
                "query_vector": q_vec,
            }
            answerable = q["kind"] == "answerable"
            gold = set(q["gold"])
            cand = retrieve(q["query"], mode="hybrid", k=depth, **common)
            if answerable:
                cand_ranks.append(metrics.first_rank([h.chunk_id for h in cand], gold, depth))
            for n in grid:
                t0 = time.perf_counter()
                hits = retrieve(
                    q["query"],
                    mode="rerank",
                    k=K,
                    rerank_n=n,
                    reranker_url=settings.reranker_url,
                    rerank_timeout=settings.rerank_timeout_s,
                    max_batch=max_batch,
                    **common,
                )
                latency[n].append((time.perf_counter() - t0) * 1000)
                for h in hits:
                    scores.setdefault((q["id"], h.chunk_id), {})[n] = h.score
                if answerable:
                    ranks[n].append(metrics.first_rank([h.chunk_id for h in hits], gold, K))
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: no report written")
        return 1
    finished = datetime.now(UTC)

    rows = []
    for n in grid:
        r_n = ranks[n]
        rows.append(
            {
                "n": n,
                "cand_recall": metrics.cand_recall(cand_ranks, n),
                **metrics.summarize(r_n),
                "hit1_ci": list(metrics.bootstrap_ci([metrics.hit_at(r, 1) for r in r_n])),
                "hit3_ci": list(metrics.bootstrap_ci([metrics.hit_at(r, 3) for r in r_n])),
                "hit5_ci": list(metrics.bootstrap_ci([metrics.hit_at(r, 5) for r in r_n])),
                "mrr10_ci": list(metrics.bootstrap_ci([metrics.rr(r, K) for r in r_n])),
                "p50_ms": percentile(latency[n], 50),
                "p95_ms": percentile(latency[n], 95),
            }
        )
    for r in rows:
        print(
            f"N={r['n']}: cand_recall {_f(r['cand_recall'])} hit1 {_f(r['hit1'])} "
            f"mrr10 {_f(r['mrr10'])} p95 {r['p95_ms']:.0f} ms"
        )
    warnings = score_inconsistencies(scores)
    for qid, cid, spread in warnings:
        print(f"WARN {qid} {cid}: score differs by {spread:.6f} across N")
    selected = select_n(rows)

    commit, dirty = git_info()
    report = {
        "started_utc": _iso(started),
        "finished_utc": _iso(finished),
        "split": split,
        "grid": grid,
        "k": K,
        "git_commit": commit,
        "git_dirty": dirty,
        "questions_sha256": questions_sha256(qs),
        "n_queries": len(qs),
        "n_answerable": n_ans,
        "reranker": {k: info.get(k) for k in ("model_id", "model_sha", "version")},
        "rows": rows,
        "p95_limit_ms": P95_LIMIT_MS,
        "mrr_tolerance": MRR_TOLERANCE,
        "selected_n": selected,
        "score_warnings": [{"qid": a, "chunk_id": b, "spread": c} for a, b, c in warnings],
    }
    reports_dir.mkdir(parents=True, exist_ok=True)
    base = reports_dir / f"rerank_sweep_{split}_{started.strftime('%Y%m%dT%H%M%SZ')}"
    base.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    base.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    if selected is None:
        return 1
    print(f"selected N = {selected}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--split", choices=evalset.SPLITS, default="dev")
    ap.add_argument("--env-file", help="path to an env file (default: ./.env if present)")
    args = ap.parse_args(argv)
    if args.split != "dev":
        print("[REFUSED] the sweep is dev-only; tuning is judged on the dev split")
        return 2
    try:
        settings = load_settings(args.env_file)
    except ConfigError as exc:
        print(f"[FAIL] config: {exc}")
        return 2
    return run_sweep(settings, args.split, client=LLMClient(settings))


if __name__ == "__main__":
    sys.exit(main())
