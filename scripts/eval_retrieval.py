"""Retrieval evaluation and pooling over eval/retrieval/questions.toml. Needs the DB and embedder.

Without --pool-only: Hit@k, MRR@10 with bootstrap CIs per mode (reports/eval/retrieval_*.json/.md).
Mode rerank needs the local reranker (docker compose up -d reranker); it is preflighted before
any gateway request. --rerank-n takes one value; a comma list is accepted only with --pool-only.
--split test appends one line per mode to eval/retrieval/test_runs.csv (never rewritten); the
rerank line uses the mode "rerank@N".
--pool-only: writes reports/eval/pool_<split>_<UTC>.csv, the unjudged candidates for labeling.
Reports hold chunk ids, ranks and scores only: no chunk text, vectors or keys. Vector scores are
cosine similarities (the store returns 1 - cosine distance); rerank scores are in [0, 1].
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import check_evalset
from rag_agent import evalset, metrics
from rag_agent.config import ConfigError, Settings, load_settings
from rag_agent.ingest import git_info
from rag_agent.llm_client import LLMClient
from rag_agent.rerank import RerankError, check_pin, percentile, reranker_info
from rag_agent.retrieve import MODES, RERANK_CANDIDATES, RERANK_MAX_BATCH, embed_query, retrieve

REPO_ROOT = Path(__file__).resolve().parent.parent
K = 10
COLLECTION = "main"
INCLUDE_SUPERSEDED = True
TEST_RUNS_HEADER = [
    "utc",
    "git_commit",
    "git_dirty",
    "questions_sha256",
    "n_queries",
    "mode",
    "hit1",
    "hit3",
    "hit5",
    "mrr10",
    "mrr10_lo",
    "mrr10_hi",
]


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _f(x: float) -> str:
    return f"{x:.4f}"


def tree_not_clean() -> bool:
    """True unless git_info() reports a clean tree: untracked files count, ignored ones do not."""
    return git_info()[1] is not False


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode("utf-8")).hexdigest()


def questions_sha256(qs: list[dict[str, Any]]) -> str:
    items = [
        {"id": q["id"], "query": q["query"], "gold": q["gold"], "related": q["related"]}
        for q in sorted(qs, key=lambda q: q["id"])
    ]
    return _sha(items)


def build_pool(
    qs: list[dict[str, Any]], runs: dict[str, dict[str, list[Any]]], depth: int, modes: list[str]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for q in qs:
        if q["kind"] != "answerable":
            continue
        judged = set(q["gold"]) | set(q["related"]) | set(q["nonrel"])
        cand: dict[str, dict[str, Any]] = {}
        for mode in modes:
            for h in runs[q["id"]][mode]:
                if h.rank > depth or h.chunk_id in judged or h.status == "superseded":
                    continue
                c = cand.setdefault(h.chunk_id, {"heading": h.heading, "ranks": {}})
                c["ranks"][mode] = h.rank
        for cid, c in cand.items():
            rows.append(
                {
                    "qid": q["id"],
                    "category": q["category"],
                    "query": q["query"],
                    "chunk_id": cid,
                    "heading": c["heading"],
                    "best_rank": min(c["ranks"].values()),
                    "ranks": " ".join(f"{m}:{c['ranks'][m]}" for m in modes if m in c["ranks"]),
                    "judgment": "",
                }
            )
    rows.sort(key=lambda r: (r["qid"], r["best_rank"], r["chunk_id"]))
    return rows


def compute_metrics(
    qs: list[dict[str, Any]], runs: dict[str, dict[str, list[Any]]], modes: list[str], truth: Any
) -> dict[str, Any]:
    ans = [q for q in qs if q["kind"] == "answerable"]
    out: dict[str, Any] = {
        "modes": {},
        "paired": {},
        "paired_hit1": {},
        "oos_top1_score": {},
        "top1_score": {},
    }
    rr_lists: dict[str, list[float]] = {}
    h1_lists: dict[str, list[float]] = {}
    for mode in modes:
        ranks = [
            metrics.first_rank([h.chunk_id for h in runs[q["id"]][mode]], set(q["gold"]), K)
            for q in ans
        ]
        rrs = [metrics.rr(r, K) for r in ranks]
        rr_lists[mode] = rrs
        h1_lists[mode] = [metrics.hit_at(r, 1) for r in ranks]
        m: dict[str, Any] = dict(metrics.summarize(ranks))
        for name, vals in (
            ("hit1", [metrics.hit_at(r, 1) for r in ranks]),
            ("hit3", [metrics.hit_at(r, 3) for r in ranks]),
            ("hit5", [metrics.hit_at(r, 5) for r in ranks]),
            ("mrr10", rrs),
        ):
            m[f"{name}_ci"] = list(metrics.bootstrap_ci(vals))
        doc_hits, sup = 0, 0
        for q in ans:
            top = runs[q["id"]][mode][:1]
            if top:
                doc_hits += top[0].doc_id in evalset.gold_docs(q, truth)
                sup += top[0].status == "superseded"
        m["doc_hit1"] = doc_hits / len(ans)
        m["superseded_at1"] = sup
        cats: dict[str, Any] = {}
        for cat in evalset.ANSWERABLE_CATEGORIES:
            idx = [i for i, q in enumerate(ans) if q["category"] == cat]
            if idx:
                cats[cat] = {
                    "n": len(idx),
                    "mrr10": sum(rrs[i] for i in idx) / len(idx),
                    "hit1": sum(metrics.hit_at(ranks[i], 1) for i in idx) / len(idx),
                }
        m["by_category"] = cats
        out["modes"][mode] = m
        out["oos_top1_score"][mode] = {
            q["id"]: (runs[q["id"]][mode][0].score if runs[q["id"]][mode] else None)
            for q in qs
            if q["kind"] == "oos"
        }
        out["top1_score"][mode] = {
            q["id"]: (runs[q["id"]][mode][0].score if runs[q["id"]][mode] else None) for q in qs
        }
    for a, b in (("hybrid", "bm25"), ("hybrid", "vector")):
        if a in rr_lists and b in rr_lists:
            d, lo, hi = metrics.paired_bootstrap_diff(rr_lists[a], rr_lists[b])
            out["paired"][f"{a}-{b}"] = {"mean_diff": d, "lo": lo, "hi": hi}
    for b in ("hybrid", "bm25", "vector"):
        if "rerank" in rr_lists and b in rr_lists:
            name = f"rerank-{b}"
            d, lo, hi = metrics.paired_bootstrap_diff(rr_lists["rerank"], rr_lists[b])
            out["paired"][name] = {"mean_diff": d, "lo": lo, "hi": hi}
            d, lo, hi = metrics.paired_bootstrap_diff(h1_lists["rerank"], h1_lists[b])
            out["paired_hit1"][name] = {"mean_diff": d, "lo": lo, "hi": hi}
    return out


def main_table(res: dict[str, Any], modes: list[str], n_ans: int) -> list[str]:
    lines = [
        "| mode | Hit@1 | Hit@3 | Hit@5 | MRR@10 [95% CI] | doc Hit@1 | superseded@1 |",
        "|---|---|---|---|---|---|---|",
    ]
    for mode in modes:
        m = res["modes"][mode]
        lo, hi = m["mrr10_ci"]
        lines.append(
            f"| {mode} | {_f(m['hit1'])} | {_f(m['hit3'])} | {_f(m['hit5'])} | "
            f"{_f(m['mrr10'])} [{_f(lo)}, {_f(hi)}] | {_f(m['doc_hit1'])} | "
            f"{m['superseded_at1']}/{n_ans} |"
        )
    return lines


def markdown(report: dict[str, Any], modes: list[str]) -> str:
    res = report["metrics"]
    out = [
        f"# Retrieval eval: {report['split']}",
        "",
        f"started {report['started_utc']}, finished {report['finished_utc']}, "
        f"commit {report['git_commit']}, dirty {report['git_dirty']}, "
        f"questions_sha256 {report['questions_sha256']}",
        "",
        f"n_queries {report['n_queries']} (answerable {report['n_answerable']}, "
        f"oos {report['n_oos']})",
        "",
        *main_table(res, modes, report["n_answerable"]),
        "",
        "## Per category (MRR@10 / Hit@1)",
        "",
        "| category | n | " + " | ".join(modes) + " |",
        "|---|---|" + "---|" * len(modes),
    ]
    for cat in evalset.ANSWERABLE_CATEGORIES:
        cells, n = [], 0
        for mode in modes:
            c = res["modes"][mode]["by_category"].get(cat)
            if c:
                n = c["n"]
                cells.append(f"{_f(c['mrr10'])} / {_f(c['hit1'])}")
            else:
                cells.append("-")
        if n:
            out.append(f"| {cat} | {n} | " + " | ".join(cells) + " |")
    out += ["", "## Paired MRR@10 differences (mean, 95% CI)", ""]
    for name, p in res["paired"].items():
        out.append(f"- {name}: {_f(p['mean_diff'])} [{_f(p['lo'])}, {_f(p['hi'])}]")
    if res.get("paired_hit1"):
        out += ["", "## Paired Hit@1 differences (mean, 95% CI)", ""]
        for name, p in res["paired_hit1"].items():
            out.append(f"- {name}: {_f(p['mean_diff'])} [{_f(p['lo'])}, {_f(p['hi'])}]")
    if "rerank" in modes:
        rr = report["reranker"]
        lat = report["rerank_latency_ms"]
        out += [
            "",
            "## Rerank",
            "",
            f"- rerank_n {report['rerank_n']}, cand_recall {_f(report['cand_recall'])}",
            f"- reranker {rr['model_id']} sha {rr['model_sha']} version {rr['version']}",
            f"- latency per call: p50 {lat['p50']:.0f} ms, p95 {lat['p95']:.0f} ms "
            f"({lat['n_calls']} calls)",
        ]
    return "\n".join(out) + "\n"


def mode_label(report: dict[str, Any], mode: str) -> str:
    return f"rerank@{report['rerank_n']}" if mode == "rerank" else mode


def append_test_runs(path: Path, report: dict[str, Any], modes: list[str]) -> None:
    new = not path.exists() or path.stat().st_size == 0
    prefix = ""
    if not new and not path.read_text(encoding="utf-8").endswith("\n"):
        prefix = "\n"
    with open(path, "a", encoding="utf-8", newline="") as fh:
        if prefix:
            fh.write(prefix)
        w = csv.writer(fh, lineterminator="\n")
        if new:
            w.writerow(TEST_RUNS_HEADER)
        for mode in modes:
            m = report["metrics"]["modes"][mode]
            w.writerow(
                [
                    report["finished_utc"],
                    report["git_commit"],
                    report["git_dirty"],
                    report["questions_sha256"],
                    report["n_queries"],
                    mode_label(report, mode),
                    _f(m["hit1"]),
                    _f(m["hit3"]),
                    _f(m["hit5"]),
                    _f(m["mrr10"]),
                    _f(m["mrr10_ci"][0]),
                    _f(m["mrr10_ci"][1]),
                ]
            )


def run_eval(
    settings: Settings,
    split: str,
    modes: list[str],
    *,
    pool_only: bool = False,
    pool_depth: int = 3,
    rerank_ns: list[int] | None = None,
    client: Any = None,
    questions_path: Path | None = None,
    docs_dir: Path | None = None,
    manifest_path: Path | None = None,
    reports_dir: Path | None = None,
    test_runs_path: Path | None = None,
) -> int:
    questions_path = questions_path or check_evalset.DEFAULT_QUESTIONS
    docs_dir = docs_dir or REPO_ROOT / "corpus" / "docs"
    manifest_path = manifest_path or REPO_ROOT / "corpus" / "manifest.toml"
    reports_dir = reports_dir or REPO_ROOT / "reports" / "eval"
    test_runs_path = test_runs_path or REPO_ROOT / "eval" / "retrieval" / "test_runs.csv"

    result = check_evalset.run_check(questions_path, docs_dir, manifest_path, evalset.LEVEL_DEFAULT)
    if result.n_fail:
        check_evalset.print_result(result)
        print("[FAIL] eval set check failed; no request sent")
        return 1
    data = evalset.load(questions_path)
    qs = evalset.split_questions(data, split)
    if not qs:
        print("no questions")
        return 1
    if split == "test" and not pool_only:
        if tree_not_clean():
            print("[REFUSED] git working tree has uncommitted or untracked files")
            return 2
        if any(q["reviewed"] is not True for q in qs):
            print("[REFUSED] some test questions are not reviewed")
            return 2

    ns = rerank_ns or [RERANK_CANDIDATES]
    has_rerank = "rerank" in modes
    if len(ns) > 1 and not pool_only:
        print("[FAIL] several --rerank-n values are accepted only with --pool-only")
        return 2
    if any(n < 1 for n in ns):
        print("[FAIL] --rerank-n must be >= 1")
        return 2

    reranker: dict[str, Any] | None = None
    max_batch = RERANK_MAX_BATCH
    if has_rerank:
        try:
            info = reranker_info(settings.reranker_url, settings.rerank_timeout_s)
            check_pin(info, settings.reranker_model_rev)
            max_batch = int(info["max_client_batch_size"])
        except (RerankError, KeyError, TypeError, ValueError) as exc:
            print(f"[FAIL] reranker preflight: {exc}; no request sent")
            return 1
        if max(ns) > max_batch:
            print(
                f"[FAIL] reranker preflight: rerank_n {max(ns)} > max_client_batch_size "
                f"{max_batch}; no request sent"
            )
            return 1
        reranker = {k: info.get(k) for k in ("model_id", "model_sha", "version")}

    # pool-only lists every N separately (rerank@N); reports use the plain mode name
    rerank_labels = [f"rerank@{n}" for n in ns] if pool_only else ["rerank"]
    labels: list[str] = []
    for mode in modes:
        labels += rerank_labels if mode == "rerank" else [mode]

    truth = evalset.build_truth(docs_dir)
    started = datetime.now(UTC)
    runs: dict[str, dict[str, list[Any]]] = {}
    cand_ranks: list[int | None] = []
    latencies: list[float] = []
    need_vec = any(m in ("vector", "hybrid", "rerank") for m in modes)
    depth = max([K, *ns]) if has_rerank else K
    common = {
        "collection": COLLECTION,
        "include_superseded": INCLUDE_SUPERSEDED,
        "client": client,
        "dsn": settings.database_url,
    }
    try:
        for q in qs:
            q_vec = embed_query(q["query"], client=client) if need_vec else None
            runs[q["id"]] = {}
            cand: list[Any] | None = None

            def hybrid_candidates(query: str = q["query"], vec: Any = q_vec) -> list[Any]:
                return retrieve(query, mode="hybrid", k=depth, query_vector=vec, **common)

            for mode in modes:
                if mode == "hybrid":
                    cand = cand or hybrid_candidates()
                    runs[q["id"]][mode] = cand[:K]
                elif mode == "rerank":
                    for n, label in zip(ns, rerank_labels, strict=True):
                        t0 = time.perf_counter()
                        runs[q["id"]][label] = retrieve(
                            q["query"],
                            mode="rerank",
                            k=K,
                            query_vector=q_vec,
                            rerank_n=n,
                            reranker_url=settings.reranker_url,
                            rerank_timeout=settings.rerank_timeout_s,
                            max_batch=max_batch,
                            **common,
                        )
                        latencies.append((time.perf_counter() - t0) * 1000)
                else:
                    runs[q["id"]][mode] = retrieve(
                        q["query"], mode=mode, k=K, query_vector=q_vec, **common
                    )
            if has_rerank and not pool_only and q["kind"] == "answerable":
                cand = cand or hybrid_candidates()
                cand_ranks.append(
                    metrics.first_rank([h.chunk_id for h in cand], set(q["gold"]), depth)
                )
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: no metrics written")
        return 1
    finished = datetime.now(UTC)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    reports_dir.mkdir(parents=True, exist_ok=True)

    if pool_only:
        rows = build_pool(qs, runs, pool_depth, labels)
        path = reports_dir / f"pool_{split}_{stamp}.csv"
        fields = [
            "qid",
            "category",
            "query",
            "chunk_id",
            "heading",
            "best_rank",
            "ranks",
            "judgment",
        ]
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        print(f"started_utc {_iso(started)}")
        print(f"finished_utc {_iso(finished)}")
        print(f"n_queries {len(qs)}")
        print(f"rows {len(rows)}")
        print(f"wrote {path}")
        return 0

    res = compute_metrics(qs, runs, modes, truth)
    commit, dirty = git_info()
    fingerprint = _sha(
        {q["id"]: {m: [h.chunk_id for h in runs[q["id"]][m]] for m in modes} for q in qs}
    )
    n_ans = sum(q["kind"] == "answerable" for q in qs)
    per_question = {}
    for q in qs:
        entry: dict[str, Any] = {"kind": q["kind"], "category": q["category"], "modes": {}}
        for mode in modes:
            hits = runs[q["id"]][mode]
            entry["modes"][mode] = {
                "first_gold_rank": metrics.first_rank([h.chunk_id for h in hits], set(q["gold"]), K)
                if q["kind"] == "answerable"
                else None,
                "top1_score": hits[0].score if hits else None,
                "top10": [{"chunk_id": h.chunk_id, "score": h.score} for h in hits],
            }
        per_question[q["id"]] = entry
    extra: dict[str, Any] = {"rerank_n": None, "reranker": None}
    if has_rerank:
        extra = {
            "rerank_n": ns[0],
            "reranker": reranker,
            "rerank_latency_ms": {
                "p50": percentile(latencies, 50),
                "p95": percentile(latencies, 95),
                "n_calls": len(latencies),
            },
            "cand_recall": metrics.cand_recall(cand_ranks, ns[0]),
            "cand_depth": depth,
        }
    report = {
        "started_utc": _iso(started),
        "finished_utc": _iso(finished),
        "split": split,
        "modes": modes,
        "k": K,
        "collection": COLLECTION,
        "include_superseded": INCLUDE_SUPERSEDED,
        "EMBED_MODEL": settings.embed_model,
        "embed_base_host": urlparse(settings.effective_embed_base_url).hostname,
        "vector_score": "cosine_similarity",
        **extra,
        "git_commit": commit,
        "git_dirty": dirty,
        "questions_sha256": questions_sha256(qs),
        "n_queries": len(qs),
        "n_answerable": n_ans,
        "n_oos": len(qs) - n_ans,
        "metrics": res,
        "questions": per_question,
        "fingerprint": fingerprint,
        "n_errors": 0,
    }
    base = reports_dir / f"retrieval_{split}_{stamp}"
    base.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    base.with_suffix(".md").write_text(markdown(report, modes), encoding="utf-8")
    if split == "test":
        append_test_runs(test_runs_path, report, modes)
    print("\n".join(main_table(res, modes, n_ans)))
    print(f"eval {split}: n_queries {len(qs)}, fingerprint {fingerprint[:12]}, ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--split", choices=evalset.SPLITS, default="dev")
    ap.add_argument("--modes", default=",".join(MODES))
    ap.add_argument(
        "--rerank-n",
        default=str(RERANK_CANDIDATES),
        help="rerank candidate size (a comma list only with --pool-only)",
    )
    ap.add_argument("--pool-only", action="store_true")
    ap.add_argument("--pool-depth", type=int, default=3)
    ap.add_argument("--env-file", help="path to an env file (default: ./.env if present)")
    args = ap.parse_args(argv)
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    bad = [m for m in modes if m not in MODES]
    if bad or not modes:
        print(f"[FAIL] --modes must be a subset of {MODES}")
        return 2
    try:
        rerank_ns = [int(x) for x in args.rerank_n.split(",") if x.strip()]
    except ValueError:
        rerank_ns = []
    if not rerank_ns or (len(rerank_ns) > 1 and not args.pool_only):
        print("[FAIL] --rerank-n needs integers; a comma list only with --pool-only")
        return 2
    try:
        settings = load_settings(args.env_file)
    except ConfigError as exc:
        print(f"[FAIL] config: {exc}")
        return 2
    return run_eval(
        settings,
        args.split,
        modes,
        pool_only=args.pool_only,
        pool_depth=args.pool_depth,
        rerank_ns=rerank_ns,
        client=LLMClient(settings),
    )


if __name__ == "__main__":
    sys.exit(main())
