"""End-to-end answer evaluation (make eval-answer SPLIT=dev REPEATS=3).

Per question: prepare once (embed, rerank retrieval, gate, context), then generate REPEATS times
when the gate passes. Sends gateway requests, so it is never part of `make test`.
Preflight (reranker pin, threshold file, prompt file) runs before any gateway request; any failure
exits 1 and writes no report. The degraded path is never used here. The test split needs --final.
Reports hold question and answer text: the eval questions are ours, not user data.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import check_evalset
from rag_agent import evalset
from rag_agent.answer import (
    AnswerResult,
    Prepared,
    Runtime,
    build_runtime,
    check_reranker,
    generate,
    prepare,
)
from rag_agent.citations import MARKER_RE, parse_markers, split_units
from rag_agent.config import ConfigError, Settings, load_settings
from rag_agent.ingest import git_info
from rag_agent.llm_client import LLMClient
from rag_agent.rerank import RerankError, percentile
from rag_agent.retrieve import RERANK_CANDIDATES

REPO_ROOT = Path(__file__).resolve().parent.parent
REASONS = ("model_declined", "uncited", "citation_invalid", "empty_output")
STAGES = ("embed", "retrieve", "rerank", "generate", "total")
REVIEW_FIELDS = [
    "qid",
    "sentence_no",
    "sentence",
    "markers",
    "cited_chunk_ids",
    "verdict",
]


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def rate(num: int, den: int) -> dict[str, Any]:
    return {"num": num, "den": den, "rate": (num / den) if den else None}


def normalize_span(text: str) -> str:
    """Lower case, markers removed, 1,000 -> 1000, '5 %' -> '5%', whitespace collapsed."""
    text = MARKER_RE.sub(" ", text).lower()
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    text = re.sub(r"\s+%", "%", text)
    return " ".join(text.split())


def span_hit(expected: str, answer_text: str) -> bool:
    span = normalize_span(expected)
    return bool(span) and span in normalize_span(answer_text)


def label_of(chunk_id: str, q: dict[str, Any]) -> str:
    if chunk_id in q["gold"]:
        return "gold"
    if chunk_id in q["related"]:
        return "related"
    if chunk_id in q["nonrel"]:
        return "nonrel"
    return "unjudged"


def make_item(
    q: dict[str, Any], repeat: int, prep: Prepared, res: AnswerResult | None
) -> dict[str, Any]:
    gate_passed = prep.terminal is None
    if res is None:  # gate only (REPEATS=0)
        res = prep.terminal or AnswerResult(status="gate_passed")
    cited = [
        {"marker": c.marker, "chunk_id": c.chunk_id, "label": label_of(c.chunk_id, q)}
        for c in res.citations
    ]
    answered = res.status == "answered"
    return {
        "qid": q["id"],
        "repeat": repeat,
        "kind": q["kind"],
        "type": q["category"],
        "query": q["query"],
        "status": res.status,
        "subtype": res.subtype,
        "reason": res.reason,
        "stage": res.stage,
        "gate_passed": gate_passed,
        "cited": cited,
        "answer_span_hit": span_hit(q["answer"], res.answer_text)
        if answered and q["kind"] == "answerable"
        else None,
        "ungrounded": res.ungrounded_number_sentences,
        "answer_text": res.answer_text,
        "call_ids": res.call_ids,
        "cost_usd": res.cost_usd,
        "model_name": res.model_name,
        "possibly_billed": res.possibly_billed,
        "prompt_tokens": res.prompt_tokens,
        "completion_tokens": res.completion_tokens,
        "context": [[s.marker, s.chunk_id] for s in prep.context],
        "latency_ms": res.latency_ms or prep.latencies,
    }


def block(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Rates (with numerators and denominators) over a set of items."""
    oos = [i for i in items if i["kind"] == "oos"]
    ans = [i for i in items if i["kind"] == "answerable"]
    passed = [i for i in ans if i["gate_passed"]]
    answered = [i for i in ans if i["status"] == "answered"]
    cited = [c for i in answered for c in i["cited"]]
    out: dict[str, Any] = {
        "correct_fallback": rate(sum(i["status"] != "answered" for i in oos), len(oos)),
        "false_fallback": rate(sum(i["status"] != "answered" for i in ans), len(ans)),
    }
    for reason in REASONS:
        out[reason] = rate(sum(i["reason"] == reason for i in passed), len(passed))
    out["cited_gold"] = rate(
        sum(any(c["label"] == "gold" for c in i["cited"]) for i in answered), len(answered)
    )
    out["cited_nonrel_or_unjudged"] = rate(
        sum(c["label"] in ("nonrel", "unjudged") for c in cited), len(cited)
    )
    out["answer_span_hit"] = rate(sum(bool(i["answer_span_hit"]) for i in answered), len(answered))
    out["ungrounded_answers"] = rate(sum(i["ungrounded"] > 0 for i in answered), len(answered))
    out["ungrounded_sentences"] = sum(i["ungrounded"] for i in answered)
    return out


def _stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None}
    return {"p50": percentile(values, 50), "p95": percentile(values, 95)}


def compute_metrics(
    items: list[dict[str, Any]], preps: list[Prepared], repeats: int
) -> dict[str, Any]:
    runs = [[i for i in items if i["repeat"] == r] for r in range(1, repeats + 1)]
    per_repeat = [block(run) for run in runs]
    graded = [i for i in items if i["repeat"] >= 1]
    summary = block(graded) if graded else {}

    if repeats:
        by_q: dict[str, list[dict[str, Any]]] = {}
        for i in graded:
            by_q.setdefault(i["qid"], []).append(i)
        summary["consistent"] = rate(
            sum(len({(i["status"], i["subtype"]) for i in v}) == 1 for v in by_q.values()),
            len(by_q),
        )
        ans_q = [v for v in by_q.values() if v[0]["kind"] == "answerable"]
        summary["answer_span_hit_all_repeats"] = rate(
            sum(all(i["answer_span_hit"] for i in v) for v in ans_q), len(ans_q)
        )
        summary["spread"] = {
            key: [
                min(r[key]["rate"] for r in per_repeat if r[key]["rate"] is not None),
                max(r[key]["rate"] for r in per_repeat if r[key]["rate"] is not None),
            ]
            for key in ("correct_fallback", "false_fallback", "cited_gold", "answer_span_hit")
            if any(r[key]["rate"] is not None for r in per_repeat)
        }

    # gate-only view, available even with REPEATS=0
    gate_items = [i for i in items if i["repeat"] == (1 if repeats else 0)]
    summary["gate"] = {
        "correct_fallback": rate(
            sum(i["kind"] == "oos" and not i["gate_passed"] for i in gate_items),
            sum(i["kind"] == "oos" for i in gate_items),
        ),
        "false_fallback": rate(
            sum(i["kind"] == "answerable" and not i["gate_passed"] for i in gate_items),
            sum(i["kind"] == "answerable" for i in gate_items),
        ),
    }

    lat: dict[str, list[float]] = {s: [] for s in STAGES}
    for p in preps:  # embed and retrieve happen once per question
        for s in ("embed", "retrieve", "rerank"):
            if p.latencies.get(s) is not None:
                lat[s].append(p.latencies[s])
    for i in graded or items:
        for s in ("generate", "total"):
            if i["latency_ms"].get(s) is not None:
                lat[s].append(i["latency_ms"][s])
    summary["latency_ms"] = {s: _stats(v) for s, v in lat.items()}
    summary["tokens"] = {
        "prompt": sum(i["prompt_tokens"] or 0 for i in graded),
        "completion": sum(i["completion_tokens"] or 0 for i in graded),
    }
    return {"per_repeat": per_repeat, "summary": summary}


def gate_summary(qs: list[dict[str, Any]], preps: list[Prepared]) -> dict[str, Any]:
    groups: dict[str, list[str]] = {}
    for q, p in zip(qs, preps, strict=True):
        t = p.terminal
        key = (
            "passed"
            if t is None
            else (t.reason or t.status)
            if t.status == "fallback"
            else t.status
        )
        groups.setdefault(key, []).append(q["id"])
    return {k: {"n": len(v), "ids": v} for k, v in sorted(groups.items())}


def review_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sentence rows for about ten answered answerable questions of repeat 1."""
    pool = sorted(
        (
            i
            for i in items
            if i["repeat"] == 1 and i["kind"] == "answerable" and i["status"] == "answered"
        ),
        key=lambda i: i["qid"],
    )
    step = max(1, len(pool) // 10)
    rows = []
    for item in pool[::step][:10]:
        chunk_of = {m: c for m, c in item["context"]}
        for n, unit in enumerate(split_units(item["answer_text"]), start=1):
            markers = list(dict.fromkeys(f"S{k}" for k in parse_markers(unit)))
            rows.append(
                {
                    "qid": item["qid"],
                    "sentence_no": n,
                    "sentence": unit,
                    "markers": ";".join(markers),
                    "cited_chunk_ids": ";".join(chunk_of.get(m, "") for m in markers),
                    "verdict": "",
                }
            )
    return rows


def _pct(r: dict[str, Any]) -> str:
    return "n/a" if r["rate"] is None else f"{r['rate']:.3f} ({r['num']}/{r['den']})"


def markdown(report: dict[str, Any]) -> str:
    s = report["metrics"]["summary"]
    lines = [
        f"# Answer eval, split {report['split']}",
        "",
        f"- started {report['started_utc']}, finished {report['finished_utc']}",
        f"- commit {report['commit']} (dirty: {report['git_dirty']})",
        f"- prompt sha256 {report['prompt_sha256']}",
        f"- threshold {report['threshold']!r}, rerank_n {report['rerank_n']}, "
        f"context_k {report['context_k']}, repeats {report['repeats']}",
        f"- models {report['model_names']}; embeddings {report['n_embed']}, chat calls "
        f"{report['n_chat_calls']}, errors {report['n_errors']}, cost {report['cost_usd']}",
        f"- complete: {report['complete']}",
        "",
        "## Gate",
        "",
    ]
    for key, v in report["gate_summary"].items():
        lines.append(f"- {key}: {v['n']} ({', '.join(v['ids'])})")
    lines += ["", "## Gate-only rates", ""]
    for key, v in s["gate"].items():
        lines.append(f"- {key}: {_pct(v)}")
    if report["repeats"]:
        lines += ["", "## End to end (all repeats pooled)", ""]
        for key, v in s.items():
            if isinstance(v, dict) and "num" in v:
                lines.append(f"- {key}: {_pct(v)}")
        lines.append(f"- ungrounded_sentences: {s['ungrounded_sentences']}")
        for key, (lo, hi) in s.get("spread", {}).items():
            lines.append(f"- spread {key}: {lo:.3f} to {hi:.3f}")
        lines += ["", "## Per repeat", ""]
        for n, b in enumerate(report["metrics"]["per_repeat"], start=1):
            lines.append(
                f"- repeat {n}: correct fallback {_pct(b['correct_fallback'])}, "
                f"false fallback {_pct(b['false_fallback'])}, "
                f"cited gold {_pct(b['cited_gold'])}, span hit {_pct(b['answer_span_hit'])}"
            )
    lines += ["", "## Latency (ms)", ""]
    for stage, st in s["latency_ms"].items():
        lines.append(f"- {stage}: p50 {st['p50']}, p95 {st['p95']}")
    lines += ["", f"tokens: {s['tokens']}", ""]
    return "\n".join(lines)


def run_eval(
    settings: Settings,
    split: str,
    repeats: int,
    *,
    rt: Runtime | None = None,
    questions_path: Path | None = None,
    docs_dir: Path | None = None,
    manifest_path: Path | None = None,
    reports_dir: Path | None = None,
    threshold_path: Path | None = None,
) -> int:
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
        print("[FAIL] no questions")
        return 1
    commit, dirty = git_info()
    if split == "test" and dirty is not False:
        print("[REFUSED] git working tree has uncommitted or untracked files")
        return 2

    # preflight: threshold file, prompt file, reranker pin; nothing is sent to the gateway yet
    try:
        if rt is None:
            rt = build_runtime(settings, client_factory=LLMClient, threshold_path=threshold_path)
    except ConfigError as exc:
        print(f"[FAIL] preflight: {exc}; no request sent")
        return 1
    problem = check_reranker(settings)
    if problem:
        print(f"[FAIL] preflight: {problem}; no request sent")
        return 1

    started = datetime.now(UTC)
    items: list[dict[str, Any]] = []
    preps: list[Prepared] = []
    n_embed = n_chat = n_errors = 0
    cost = 0.0
    models: set[str] = set()
    try:
        for q in qs:
            prep = prepare(q["query"], rt, allow_degraded=False)
            preps.append(prep)
            n_embed += prep.embedded
            if prep.terminal is not None and prep.terminal.status == "error":
                n_errors += 1
            if repeats == 0:
                items.append(make_item(q, 0, prep, None))
                continue
            for r in range(1, repeats + 1):
                res = generate(prep, rt) if prep.terminal is None else prep.terminal
                if prep.terminal is None:
                    n_chat += res.n_chat_calls
                    n_errors += res.status == "error"
                    cost += res.cost_usd or 0.0
                    if res.model_name:
                        models.add(res.model_name)
                items.append(make_item(q, r, prep, res))
    except RerankError as exc:
        print(f"[FAIL] reranker failed during the run ({type(exc).__name__}); no report written")
        return 1

    finished = datetime.now(UTC)
    complete = n_errors == 0
    report = {
        "split": split,
        "started_utc": _iso(started),
        "finished_utc": _iso(finished),
        "commit": commit,
        "git_dirty": dirty,
        "prompt_sha256": rt.prompt_sha256,
        "threshold": rt.threshold,
        "rerank_n": RERANK_CANDIDATES,
        "context_k": settings.answer_context_k,
        "repeats": repeats,
        "model_names": sorted(models),
        "n_embed": n_embed,
        "n_chat_calls": n_chat,
        "n_errors": n_errors,
        "cost_usd": cost,
        "complete": complete,
        "gate_summary": gate_summary(qs, preps),
        "metrics": compute_metrics(items, preps, repeats),
        "items": items,
    }
    reports_dir.mkdir(parents=True, exist_ok=True)
    base = reports_dir / f"answer_{split}_{_stamp(started)}"
    base.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    base.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    if repeats:
        sheet = reports_dir / f"answer_review_{split}_{_stamp(started)}.csv"
        with sheet.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=REVIEW_FIELDS)
            writer.writeheader()
            writer.writerows(review_rows(items))
        print(f"wrote {sheet}")
    print(f"wrote {base.with_suffix('.json')}")
    if not complete:
        print(f"[INCOMPLETE] {n_errors} error(s); report written with complete=false")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--split", choices=evalset.SPLITS, default="dev")
    ap.add_argument("--repeats", type=int, default=3, help="generate N times per question")
    ap.add_argument("--final", action="store_true", help="required for the test split")
    ap.add_argument("--env-file", help="path to an env file (default: ./.env if present)")
    args = ap.parse_args(argv)
    if args.split == "test" and not args.final:
        print("[REFUSED] the test split needs --final")
        return 2
    if args.repeats < 0:
        print("[FAIL] --repeats must be >= 0")
        return 2
    try:
        settings = load_settings(args.env_file)
    except ConfigError as exc:
        print(f"[FAIL] config: {exc}")
        return 2
    return run_eval(settings, args.split, args.repeats)


if __name__ == "__main__":
    sys.exit(main())
