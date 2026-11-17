"""Aggregation and rendering for agent evaluation reports (D8). Pure: dicts in, dicts and text out.

A run record is one attempt: the task, repeat and attempt numbers, `superseded`, the user and
question, the fields of asdict(AgentResult) at the top level, the database rows the checks read
(requests, drafts, audit), the checks and `passed`. Only records with superseded false are scored.
The JSON keys are fixed because the manual's jq commands read them.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from typing import Any

from rag_agent import citations, metrics, rerank
from rag_agent.agent.mockdata import User
from rag_agent.agent_eval import checks
from rag_agent.agent_eval.checks import BEHAVIOUR, POLICY, Check, World
from rag_agent.agent_eval.tasks import Task

TOP_KEYS = ("meta", "policy", "behaviour", "citation", "numbers", "efficiency", "runs")
META_KEYS = (
    "utc",
    "split",
    "model_alias",
    "model_names",
    "git_commit",
    "git_dirty",
    "tasks_sha256",
    "prompt_sha256",
    "tools_lock_sha256",
    "policy_sha256",
    "repeats",
    "n_runs",
    "n_reruns",
    "n_chat_calls",
    "n_embed_calls",
    "cost_usd",
    "llm_timeout_s",
    "llm_max_retries",
)
# meta keys the runner supplies; the others are derived from the run records
BASE_META_KEYS = tuple(
    k
    for k in META_KEYS
    if k not in ("model_names", "n_runs", "n_reruns", "n_chat_calls", "n_embed_calls", "cost_usd")
)
LEDGER_HEADER = (
    "utc",
    "git_commit",
    "tasks_sha256",
    "prompt_sha256",
    "model_alias",
    "model_name",
    "repeats",
    "n_runs",
    "n_reruns",
    "policy_passed",
    "policy_total",
    "runs_passed",
    "pass_at_1",
    "ci_lo",
    "ci_hi",
    "tasks_all_pass",
    "n_tasks",
    "cost_usd",
)
REVIEW_FIELDS = ("task", "repeat", "unit", "verdict", "note")
FAILURE_FIELDS = ("task", "repeat", "run_id", "failed_checks", "cause", "note")
VERDICTS = ("supported", "partial", "unsupported")
CAUSES = ("model", "checker", "task")
CITE_CHECK = "cites known sources"
REVIEW_SAMPLE = 10
SAME_KEYS = ("split", "repeats", "tasks_sha256", "prompt_sha256")


class TallyError(Exception):
    pass


class RescoreError(Exception):
    pass


class CompareError(Exception):
    pass


# --- run records -------------------------------------------------------------------------------


def jsonable(value: Any) -> Any:
    """The value as it reads back from the report JSON (datetimes and decimals become strings)."""
    return json.loads(json.dumps(value, default=str))


def run_record(
    task: Task,
    repeat: int,
    attempt: int,
    superseded: bool,
    question: str,
    res: dict[str, Any],
    requests: list[dict[str, Any]],
    drafts: list[dict[str, Any]],
    audit: dict[str, Any],
    run_checks: Sequence[Check],
    passed: bool | None,
) -> dict[str, Any]:
    return {
        "task": task.id,
        "repeat": repeat,
        "attempt": attempt,
        "superseded": superseded,
        "user": task.user,
        "question": question,
        **res,
        "requests": requests,
        "drafts": drafts,
        "audit": audit,
        "checks": [asdict(c) for c in run_checks],
        "passed": passed,
    }


def scored(runs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in runs if not r["superseded"]]


def failed_checks(run: dict[str, Any], kind: str | None = None) -> list[str]:
    return [
        c["name"] for c in run["checks"] if not c["passed"] and (kind is None or c["kind"] == kind)
    ]


def task_order(runs: Iterable[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(r["task"] for r in runs))


def per_task(runs: Iterable[dict[str, Any]]) -> dict[str, tuple[int, int]]:
    """task id -> (passed runs k, scored runs N), in run order."""
    out: dict[str, tuple[int, int]] = {}
    for r in scored(runs):
        k, n = out.get(r["task"], (0, 0))
        out[r["task"]] = (k + (1 if r["passed"] else 0), n + 1)
    return out


# --- aggregation -------------------------------------------------------------------------------


def _round(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(x, nd)


def _group(kn: Mapping[str, tuple[int, int]], ids: Iterable[str]) -> dict[str, Any]:
    ids = [i for i in ids if i in kn]
    runs_passed = sum(kn[i][0] for i in ids)
    n_runs = sum(kn[i][1] for i in ids)
    all_pass = sum(1 for i in ids if kn[i][0] == kn[i][1])
    return {
        "pass_at_1": _round(runs_passed / n_runs) if n_runs else None,
        "runs_passed": runs_passed,
        "n_runs": n_runs,
        "pass_all": _round(all_pass / len(ids)) if ids else None,
        "tasks_all_pass": all_pass,
        "n_tasks": len(ids),
    }


def _stats(values: Sequence[float | None]) -> dict[str, float | None]:
    xs = [float(v) for v in values if v is not None]
    if not xs:
        return {"mean": None, "p50": None, "p95": None}
    return {
        "mean": _round(sum(xs) / len(xs), 6),
        "p50": _round(rerank.percentile(xs, 50), 6),
        "p95": _round(rerank.percentile(xs, 95), 6),
    }


def meta_for(runs: Sequence[dict[str, Any]], base: Mapping[str, Any]) -> dict[str, Any]:
    """The 18 meta keys. Counts of calls and cost cover every attempt (superseded included);
    n_runs counts scored runs only; n_reruns counts superseded attempts."""
    names: list[str] = []
    for r in runs:
        names += [n for n in r.get("model_names") or [] if n not in names]
    costs = [r["cost_usd"] for r in runs if r.get("cost_usd") is not None]
    derived = {
        "model_names": names,
        "n_runs": len(scored(runs)),
        "n_reruns": sum(1 for r in runs if r["superseded"]),
        "n_chat_calls": sum(r.get("n_chat_calls", 0) for r in runs),
        "n_embed_calls": sum(r.get("n_embed_calls", 0) for r in runs),
        "cost_usd": _round(sum(costs), 6) if costs else None,
    }
    return {k: derived[k] if k in derived else base.get(k) for k in META_KEYS}


def summarize(
    runs: list[dict[str, Any]],
    tasks: Mapping[str, Task],
    users: Mapping[str, User],
    base_meta: Mapping[str, Any],
) -> dict[str, Any]:
    done = scored(runs)
    kn = per_task(runs)
    ids = task_order(done)

    # policy
    pol = [c for r in done for c in r["checks"] if c["kind"] == POLICY]

    # behaviour
    overall = _group(kn, ids)
    if ids:
        lo, hi = metrics.bootstrap_ci([kn[i][0] / kn[i][1] for i in ids])
    else:
        lo = hi = None
    categories = list(dict.fromkeys(tasks[i].category for i in ids))
    roles = list(dict.fromkeys(users[tasks[i].user].role for i in ids))
    behaviour = {
        "pass_at_1": {
            "value": overall["pass_at_1"],
            "ci_lo": _round(lo),
            "ci_hi": _round(hi),
            "runs_passed": overall["runs_passed"],
            "n_runs": overall["n_runs"],
        },
        "pass_all": {
            "value": overall["pass_all"],
            "tasks_all_pass": overall["tasks_all_pass"],
            "n_tasks": overall["n_tasks"],
        },
        "unstable": [i for i in ids if 0 < kn[i][0] < kn[i][1]],
        "by_category": {
            c: _group(kn, [i for i in ids if tasks[i].category == c]) for c in categories
        },
        "by_role": {
            role: _group(kn, [i for i in ids if users[tasks[i].user].role == role])
            for role in roles
        },
    }

    # citation
    cite = [c for r in done for c in r["checks"] if c["name"] == CITE_CHECK]
    gold_runs = [r for r in done if tasks[r["task"]].gold_docs and r["cited_markers"]]
    citation = {
        "no_unknown": {"runs": sum(1 for r in done if not r["unknown_markers"]), "of": len(done)},
        "cite_checks": {"passed": sum(1 for c in cite if c["passed"]), "total": len(cite)},
        "gold_doc_hits": {
            "hits": sum(1 for r in gold_runs if checks.gold_doc_hit(tasks[r["task"]], r)),
            "of": len(gold_runs),
        },
    }

    # numbers
    ungrounded = []
    for r in done:
        units = checks.ungrounded_units(r, r["question"])
        if units:
            ungrounded.append(
                {"task": r["task"], "repeat": r["repeat"], "run_id": r["run_id"], "units": units}
            )
    numbers = {
        "ungrounded_units": sum(len(u["units"]) for u in ungrounded),
        "runs": ungrounded,
    }

    # efficiency, cost, latency
    per_run = []
    for r in done:
        required = checks.required_tool_count(tasks[r["task"]])
        per_run.append(
            {
                "task": r["task"],
                "repeat": r["repeat"],
                "n_chat_calls": r["n_chat_calls"],
                "n_tool_calls": r["n_tool_calls"],
                "tool_ratio": _round(r["n_tool_calls"] / required) if required else None,
                "cost_usd": r["cost_usd"],
                "latency_ms_total": r["latency_ms_total"],
            }
        )
    scored_cost = sum(p["cost_usd"] or 0.0 for p in per_run)
    efficiency = {
        "per_run": per_run,
        "n_chat_calls": _stats([p["n_chat_calls"] for p in per_run]),
        "n_tool_calls": _stats([p["n_tool_calls"] for p in per_run]),
        "tool_ratio": _stats([p["tool_ratio"] for p in per_run]),
        "cost_usd": {
            **_stats([p["cost_usd"] for p in per_run]),
            "total_scored": _round(scored_cost, 6),
            "mean_per_task": _round(scored_cost / len(ids), 6) if ids else None,
        },
        "latency_ms_total": _stats([p["latency_ms_total"] for p in per_run]),
    }

    return {
        "meta": meta_for(runs, base_meta),
        "policy": {"passed": sum(1 for c in pol if c["passed"]), "total": len(pol)},
        "behaviour": behaviour,
        "citation": citation,
        "numbers": numbers,
        "efficiency": efficiency,
        "runs": runs,
    }


# --- markdown ----------------------------------------------------------------------------------


def fmt(x: Any, nd: int = 3) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def tool_sequence(run: dict[str, Any]) -> str:
    return " > ".join(f"{t['tool']}:{t['status']}" for t in run["tool_trace"]) or "-"


def _group_table(title: str, groups: Mapping[str, Mapping[str, Any]]) -> list[str]:
    lines = [
        f"### {title}",
        "",
        f"{title.split()[-1]} | pass@1 | runs | pass-all | tasks",
        "--- | --- | --- | --- | ---",
    ]
    for name, g in groups.items():
        lines.append(
            f"{name} | {fmt(g['pass_at_1'])} | {g['runs_passed']}/{g['n_runs']}"
            f" | {fmt(g['pass_all'])} | {g['tasks_all_pass']}/{g['n_tasks']}"
        )
    return [*lines, ""]


def render_markdown(report: dict[str, Any], title: str = "Agent evaluation") -> str:
    m, b, c, e = report["meta"], report["behaviour"], report["citation"], report["efficiency"]
    p1, pa = b["pass_at_1"], b["pass_all"]
    lines = [
        f"# {title}: {m['split']} / {m['model_alias']} / {m['utc']}",
        "",
        "key | value",
        "--- | ---",
        *[
            f"{k} | {fmt(v) if not isinstance(v, list) else ', '.join(v) or '-'}"
            for k, v in m.items()
        ],
        "",
        "## Summary",
        "",
        "measure | value",
        "--- | ---",
        f"policy checks | {report['policy']['passed']}/{report['policy']['total']}",
        f"pass@1 | {fmt(p1['value'])} [{fmt(p1['ci_lo'])}, {fmt(p1['ci_hi'])}]"
        f" ({p1['runs_passed']}/{p1['n_runs']} runs)",
        f"pass-all | {fmt(pa['value'])} ({pa['tasks_all_pass']}/{pa['n_tasks']} tasks)",
        f"unstable | {', '.join(b['unstable']) or '-'}",
        f"runs with no unknown marker | {c['no_unknown']['runs']}/{c['no_unknown']['of']}",
        f"cite checks passed | {c['cite_checks']['passed']}/{c['cite_checks']['total']}",
        f"gold-doc hits | {c['gold_doc_hits']['hits']}/{c['gold_doc_hits']['of']}",
        f"ungrounded number units | {report['numbers']['ungrounded_units']}"
        f" in {len(report['numbers']['runs'])} runs",
        "",
        *_group_table("By category", b["by_category"]),
        *_group_table("By role", b["by_role"]),
        "## Efficiency, cost, latency (scored runs)",
        "",
        "measure | mean | p50 | p95",
        "--- | --- | --- | ---",
    ]
    for key in ("n_chat_calls", "n_tool_calls", "tool_ratio", "cost_usd", "latency_ms_total"):
        s = e[key]
        lines.append(f"{key} | {fmt(s['mean'], 6)} | {fmt(s['p50'], 6)} | {fmt(s['p95'], 6)}")
    lines += ["", f"mean cost per task: {fmt(e['cost_usd']['mean_per_task'], 6)}", ""]
    if report["numbers"]["runs"]:
        lines += ["## Ungrounded number units", ""]
        for u in report["numbers"]["runs"]:
            lines += [f"- {u['task']} r{u['repeat']} ({u['run_id']}): {len(u['units'])}"]
        lines.append("")
    for r in scored(report["runs"]):
        if r["passed"]:
            continue
        lines += [
            f"## {r['task']} r{r['repeat']} ({r['run_id']}) status {r['status']}",
            "",
            r["final_text"] or "(no final text)",
            "",
            f"Tools: {tool_sequence(r)}",
            "",
            "Failed: " + "; ".join(failed_checks(r)),
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


# --- review, failures, ledger ------------------------------------------------------------------


def review_runs(report: dict[str, Any], tasks: Mapping[str, Task]) -> list[dict[str, Any]]:
    """First repeat only: REVIEW_SAMPLE runs at equal steps over the task ids in order, plus the
    first repeat of every `awaiting` task; in task order, no duplicates."""
    first = {r["task"]: r for r in scored(report["runs"]) if r["repeat"] == 1}
    ids = [i for i in task_order(scored(report["runs"])) if i in first]
    n = len(ids)
    if n <= REVIEW_SAMPLE:
        picked = set(ids)
    else:
        picked = {ids[i * n // REVIEW_SAMPLE] for i in range(REVIEW_SAMPLE)}
    picked |= {i for i in ids if tasks[i].category == "awaiting"}
    return [first[i] for i in ids if i in picked]


def review_rows(report: dict[str, Any], tasks: Mapping[str, Task]) -> list[dict[str, Any]]:
    return [
        {"task": r["task"], "repeat": r["repeat"], "unit": unit, "verdict": "", "note": ""}
        for r in review_runs(report, tasks)
        for unit in citations.split_units(r["final_text"])
    ]


def failure_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "task": r["task"],
            "repeat": r["repeat"],
            "run_id": r["run_id"],
            "failed_checks": "; ".join(failed_checks(r, BEHAVIOUR)),
            "cause": "",
            "note": "",
        }
        for r in scored(report["runs"])
        if failed_checks(r, BEHAVIOUR)
    ]


def ledger_row(report: dict[str, Any]) -> list[str]:
    m, b = report["meta"], report["behaviour"]
    values = {
        "utc": m["utc"],
        "git_commit": m["git_commit"],
        "tasks_sha256": m["tasks_sha256"],
        "prompt_sha256": m["prompt_sha256"],
        "model_alias": m["model_alias"],
        "model_name": "|".join(m["model_names"]),
        "repeats": m["repeats"],
        "n_runs": m["n_runs"],
        "n_reruns": m["n_reruns"],
        "policy_passed": report["policy"]["passed"],
        "policy_total": report["policy"]["total"],
        "runs_passed": b["pass_at_1"]["runs_passed"],
        "pass_at_1": fmt(b["pass_at_1"]["value"], 4),
        "ci_lo": fmt(b["pass_at_1"]["ci_lo"], 4),
        "ci_hi": fmt(b["pass_at_1"]["ci_hi"], 4),
        "tasks_all_pass": b["pass_all"]["tasks_all_pass"],
        "n_tasks": b["pass_all"]["n_tasks"],
        "cost_usd": fmt(m["cost_usd"], 6),
    }
    return [str(values[k]) for k in LEDGER_HEADER]


# --- tally -------------------------------------------------------------------------------------


def _tally(
    rows: Sequence[Mapping[str, Any]], column: str, allowed: Sequence[str]
) -> dict[str, int]:
    counts = dict.fromkeys(allowed, 0)
    for i, row in enumerate(rows, start=2):  # line 1 is the header
        value = (row.get(column) or "").strip()
        if not value:
            raise TallyError(f"line {i}: empty {column}")
        if value not in allowed:
            raise TallyError(f"line {i}: {column} {value!r} is not one of {', '.join(allowed)}")
        counts[value] += 1
    return counts


def tally_review(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = _tally(rows, "verdict", VERDICTS)
    return {**counts, "tasks": len({r["task"] for r in rows})}


def tally_failures(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return _tally(rows, "cause", CAUSES)


# --- rescore -----------------------------------------------------------------------------------


RESCORE_META_KEYS = ("rescored_tasks_sha256", "rescored_utc")


def rescore(
    report: dict[str, Any],
    tasks: Mapping[str, Task],
    world: World,
    loans: dict[str, str],
    *,
    tasks_sha256: str,
    utc: str,
) -> dict[str, Any]:
    """Re-apply the current checks to the stored scored runs; superseded records are copied.

    The stored meta is kept as it was; meta.rescored_tasks_sha256 (the task file used for the
    new judgement) and meta.rescored_utc are added after it."""
    runs = []
    for r in report["runs"]:
        if r["superseded"]:
            runs.append(r)
            continue
        task = tasks.get(r["task"])
        if task is None:
            raise RescoreError(f"task {r['task']} is not in the task file")
        v = checks.judge(
            task,
            r,
            audit=r["audit"],
            requests=r["requests"],
            drafts=r["drafts"],
            world=world,
            loans=loans,
        )
        runs.append(
            {**r, "checks": [asdict(c) for c in [*v.behaviour, *v.policy]], "passed": v.passed}
        )
    base = {k: report["meta"].get(k) for k in BASE_META_KEYS}
    out = summarize(runs, tasks, world.users, base)
    stored = {k: v for k, v in report["meta"].items() if k not in RESCORE_META_KEYS}
    out["meta"] = {**stored, "rescored_tasks_sha256": tasks_sha256, "rescored_utc": utc}
    return out


def totals(report: dict[str, Any]) -> dict[str, int]:
    p1 = report["behaviour"]["pass_at_1"]
    return {
        "policy_passed": report["policy"]["passed"],
        "policy_total": report["policy"]["total"],
        "runs_passed": p1["runs_passed"],
        "n_runs": p1["n_runs"],
    }


# --- compare -----------------------------------------------------------------------------------


def compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    for key in SAME_KEYS:
        if a["meta"].get(key) != b["meta"].get(key):
            raise CompareError(f"{key} differs: {a['meta'].get(key)} vs {b['meta'].get(key)}")
    ka, kb = per_task(a["runs"]), per_task(b["runs"])
    if set(ka) != set(kb):
        raise CompareError("the reports cover different tasks")
    ids = list(ka)
    va = [ka[i][0] / ka[i][1] for i in ids]
    vb = [kb[i][0] / kb[i][1] for i in ids]
    diff, lo, hi = metrics.paired_bootstrap_diff(va, vb)

    def side(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "model_alias": r["meta"]["model_alias"],
            "model_names": r["meta"]["model_names"],
            "pass_at_1": r["behaviour"]["pass_at_1"],
            "pass_all": r["behaviour"]["pass_all"],
            "policy": r["policy"],
            "by_category": r["behaviour"]["by_category"],
            "mean_cost_per_task": r["efficiency"]["cost_usd"]["mean_per_task"],
            "latency_p50": r["efficiency"]["latency_ms_total"]["p50"],
            "latency_p95": r["efficiency"]["latency_ms_total"]["p95"],
        }

    return {
        "split": a["meta"]["split"],
        "repeats": a["meta"]["repeats"],
        "n_tasks": len(ids),
        "a": side(a),
        "b": side(b),
        "diff": {"mean": _round(diff), "ci_lo": _round(lo), "ci_hi": _round(hi)},
    }


def render_compare(cmp: dict[str, Any], utc: str) -> str:
    a, b, d = cmp["a"], cmp["b"], cmp["diff"]

    def p1(s: dict[str, Any]) -> str:
        p = s["pass_at_1"]
        return f"{fmt(p['value'])} [{fmt(p['ci_lo'])}, {fmt(p['ci_hi'])}]"

    def pa(s: dict[str, Any]) -> str:
        p = s["pass_all"]
        return f"{fmt(p['value'])} ({p['tasks_all_pass']}/{p['n_tasks']})"

    lines = [
        f"# Agent eval comparison {utc}",
        "",
        f"split {cmp['split']}, repeats {cmp['repeats']}, tasks {cmp['n_tasks']}",
        "",
        f"measure | A {a['model_alias']} | B {b['model_alias']}",
        "--- | --- | ---",
        f"model names | {', '.join(a['model_names']) or '-'}"
        f" | {', '.join(b['model_names']) or '-'}",
        f"pass@1 [95% CI] | {p1(a)} | {p1(b)}",
        f"pass-all | {pa(a)} | {pa(b)}",
        f"policy checks | {a['policy']['passed']}/{a['policy']['total']}"
        f" | {b['policy']['passed']}/{b['policy']['total']}",
        f"mean cost per task | {fmt(a['mean_cost_per_task'], 6)}"
        f" | {fmt(b['mean_cost_per_task'], 6)}",
        f"latency p50 / p95 ms | {fmt(a['latency_p50'], 1)} / {fmt(a['latency_p95'], 1)}"
        f" | {fmt(b['latency_p50'], 1)} / {fmt(b['latency_p95'], 1)}",
        "",
        f"paired difference A - B of per-task k/N: {fmt(d['mean'])}"
        f" [{fmt(d['ci_lo'])}, {fmt(d['ci_hi'])}]",
        "",
        "category | A pass@1 | A pass-all | B pass@1 | B pass-all",
        "--- | --- | --- | --- | ---",
    ]
    for cat in dict.fromkeys([*a["by_category"], *b["by_category"]]):
        ga, gb = a["by_category"].get(cat), b["by_category"].get(cat)
        cells = []
        for g in (ga, gb):
            cells += (
                [fmt(g["pass_at_1"]), f"{g['tasks_all_pass']}/{g['n_tasks']}"] if g else ["-", "-"]
            )
        lines.append(f"{cat} | " + " | ".join(cells))
    return "\n".join(lines) + "\n"
