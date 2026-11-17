"""Agent evaluation (D8).

check   [--tasks PATH]
        Load eval/agent/tasks.toml, resolve every loan selector against the mock data and verify
        all gold values against the code. No network, no database. Exit 1 on the first error,
        printed as "<where>: <what>".
run     --split dev|test --repeats N [--model ALIAS] [--final] [--cost-cap USD] [--only ID,ID]
        [--pace SECONDS] [--tasks PATH]
        Run every task of the split N times through the agent loop (gateway requests). Preflight
        before any request; reset and setup before every run; policy failure, a second gateway
        error, a fatal gateway error, a missing cost header or the cost cap abort (exit 1,
        aborted_<UTC>.json, no report, no ledger line). The test split needs --final and a clean
        tree and appends one line to eval/agent/test_runs.csv.
prescan [--tasks PATH]
        Send every task's question once through the gateway (max_tokens 16). Gateway mode only.
tally   FILE
        Count verdicts of a review CSV or causes of a failures CSV. Exit 1 on an empty or unknown
        value.
rescore REPORT.json [--allow-new-tasks] [--tasks PATH]
        Re-apply the current checks to the stored runs (no gateway, no database).
compare A.json B.json
        Compare two reports of the same split, repeats, task file and prompt.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

import agent_audit
import agent_db_check
from rag_agent import store
from rag_agent.agent import loop, mockdata, specs
from rag_agent.agent.db import PgRepo, default_dsn
from rag_agent.agent.errors import AgentStartupError
from rag_agent.agent.policy_config import load_policy
from rag_agent.agent_eval import dbstate
from rag_agent.agent_eval import report as rpt
from rag_agent.agent_eval.checks import POLICY, World, judge
from rag_agent.agent_eval.tasks import Task, TaskFileError, TaskSet, load_tasks, question_for
from rag_agent.answer import REPO_ROOT, load_prompt
from rag_agent.config import ConfigError, Settings, load_settings
from rag_agent.ingest import git_info
from rag_agent.llm_client import LLMClient

LEDGER_PATH = REPO_ROOT / "eval" / "agent" / "test_runs.csv"
DEFAULT_COST_CAP = 1.00
# gateway error category -> seconds to wait before the one rerun; any other category aborts
RERUN_WAIT = {"rate_limited": 65.0, "timeout": 10.0, "gateway_unavailable": 10.0}
GATEWAY_STATUSES = ("error", "blocked")
PRESCAN_PAUSE = 0.2


# --- check -------------------------------------------------------------------------------------


def _task_keys(task) -> str:
    keys = [
        name
        for name, present in (
            ("status", task.status),
            ("ok", task.ok),
            ("not_ok", task.not_ok),
            ("order", task.order),
            ("has", task.has),
            ("has_any", task.has_any),
            ("lacks", task.lacks),
            ("cite", task.cite),
            ("drafts", task.drafts is not None),
            ("draft_to", task.draft_to),
            ("request", task.request),
            ("asks", task.asks),
            ("policy", task.policy),
            ("setup", task.setup),
        )
        if present
    ]
    return " ".join(keys)


def render(ts: TaskSet) -> list[str]:
    lines = []
    for name, sel in ts.selectors.items():
        filters = [
            f"{k}={v}"
            for k, v in (
                ("band", sel.band),
                ("in_progress", sel.in_progress),
                ("stage", sel.stage),
                ("min_conditions", sel.min_conditions),
                ("frequency", sel.frequency),
            )
            if v is not None
        ]
        lines.append(
            f"{name:<12} {sel.who:<6} -> {ts.loans[name]}  {' '.join(filters) or '-'}"
            f"  gold ok ({len(sel.gold)})"
        )
    for task in ts.tasks:
        lines.append(
            f"{task.id:<4} {task.split:<4} {task.category:<14} {task.user:<6} "
            f"checks: {_task_keys(task)}"
        )
    dev = sum(1 for t in ts.tasks if t.split == "dev")
    test = sum(1 for t in ts.tasks if t.split == "test")
    lines.append(
        f"tasks: {len(ts.tasks)} (dev {dev}, test {test}); selectors: {len(ts.selectors)};"
        " gold mismatches: 0"
    )
    return lines


def cmd_check(args: argparse.Namespace, env: Env) -> int:
    try:
        ts = load_tasks(args.tasks)
    except TaskFileError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("\n".join(render(ts)))
    return 0


# --- the runner core (no network, no database of its own) --------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Deps:
    """Everything a run touches. The real ones talk to the gateway and Postgres; tests fake them."""

    run_fn: Callable[[str, str], dict[str, Any]]  # (question, user) -> asdict(AgentResult)
    repo: Any  # list_requests()
    audit_fn: Callable[[], dict[str, Any]]
    reset_fn: Callable[[], None]
    drafts_fn: Callable[[str], list[dict[str, Any]]]
    setup_fn: Callable[[Task], Any]
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], datetime] = _now


@dataclass
class Outcome:
    runs: list[dict[str, Any]]
    aborted: str | None
    cost_usd: float


class Abort(Exception):
    pass


def _row(rec: dict[str, Any]) -> str:
    cost = "n/a" if rec["cost_usd"] is None else f"{rec['cost_usd']:.6f}"
    result = "-" if rec["passed"] is None else ("PASS" if rec["passed"] else "FAIL")
    return (
        f"{rec['task']} r{rec['repeat']} a{rec['attempt']} | {rec['status']} |"
        f" {rec['reason'] or '-'} | {rpt.tool_sequence(rec)} | {rec['n_chat_calls']} | {cost}"
        f" | {result}"
    )


def _attempt(
    task: Task,
    repeat: int,
    attempt: int,
    question: str,
    loans: dict[str, str],
    world: World,
    deps: Deps,
) -> tuple[dict[str, Any], str | None]:
    """reset -> setup -> run -> read rows -> judge. Returns the record and the gateway error
    category (None when the run did not end in a gateway error)."""
    deps.reset_fn()
    deps.setup_fn(task)
    res = rpt.jsonable(deps.run_fn(question, task.user))
    requests = rpt.jsonable(deps.repo.list_requests())
    drafts = rpt.jsonable(deps.drafts_fn(res["run_id"]))
    audit = rpt.jsonable(deps.audit_fn())
    v = judge(task, res, audit=audit, requests=requests, drafts=drafts, world=world, loans=loans)
    gateway = (res["reason"] or "unknown") if res["status"] in GATEWAY_STATUSES else None
    if gateway is not None:  # not scored: only the policy checks are kept
        run_checks, passed = list(v.policy), None
    else:
        run_checks, passed = [*v.behaviour, *v.policy], v.passed
    rec = rpt.run_record(
        task, repeat, attempt, False, question, res, requests, drafts, audit, run_checks, passed
    )
    return rec, gateway


def run_tasks(
    tasks: Sequence[Task],
    loans: dict[str, str],
    world: World,
    *,
    repeats: int,
    pace: float,
    cost_cap: float,
    deps: Deps,
    out: Callable[[str], None] = print,
) -> Outcome:
    """Every task x repeat in order, sequentially, with the rerun and abort rules."""
    runs: list[dict[str, Any]] = []
    cost = 0.0
    first = True
    try:
        for task in tasks:
            question = question_for(task, loans)
            for repeat in range(1, repeats + 1):
                if not first and pace > 0:
                    deps.sleep(pace)
                first = False
                attempt = 1
                while True:
                    rec, gateway = _attempt(task, repeat, attempt, question, loans, world, deps)
                    runs.append(rec)
                    cost += rec["cost_usd"] or 0.0
                    out(_row(rec))
                    bad = rpt.failed_checks(rec, POLICY)
                    if bad:
                        out(f"[FAIL] policy {task.id} r{repeat} {rec['run_id']}: {'; '.join(bad)}")
                        raise Abort(f"policy check failed in {task.id} r{repeat}")
                    if gateway is None and rec["n_chat_calls"] > 0 and rec["cost_usd"] is None:
                        raise Abort(f"cost header missing in {task.id} r{repeat}")
                    if cost > cost_cap:
                        raise Abort(f"cost cap exceeded ({cost:.6f} > {cost_cap:.2f} USD)")
                    if gateway is None:
                        break
                    wait = RERUN_WAIT.get(gateway)
                    if wait is None:
                        raise Abort(f"gateway error {gateway} in {task.id} r{repeat}")
                    if attempt > 1:
                        raise Abort(f"second gateway error ({gateway}) in {task.id} r{repeat}")
                    rec["superseded"] = True
                    out(f"  {gateway}: waiting {wait:g} s, then rerunning {task.id} r{repeat}")
                    deps.sleep(wait)
                    attempt += 1
    except Abort as exc:
        return Outcome(runs, str(exc), cost)
    except Exception as exc:  # setup or database failure: stop, keep what was collected
        return Outcome(runs, f"unexpected error ({type(exc).__name__})", cost)
    return Outcome(runs, None, cost)


# --- files -------------------------------------------------------------------------------------


def _dir(path: str | Path) -> Path:
    d = Path(path)
    d = d if d.is_absolute() else REPO_ROOT / d
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", text) or "model"


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, fields: Sequence[str], rows: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fields), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def write_outputs(
    report: dict[str, Any], out_dir: Path, stem: str, tasks: dict[str, Task]
) -> list[Path]:
    """eval_<stem>.json/.md, review_<stem>.csv, failures_<stem>.csv."""
    paths = [
        out_dir / f"eval_{stem}.json",
        out_dir / f"eval_{stem}.md",
        out_dir / f"review_{stem}.csv",
        out_dir / f"failures_{stem}.csv",
    ]
    _write_json(paths[0], report)
    paths[1].write_text(rpt.render_markdown(report), encoding="utf-8")
    _write_csv(paths[2], rpt.REVIEW_FIELDS, rpt.review_rows(report, tasks))
    _write_csv(paths[3], rpt.FAILURE_FIELDS, rpt.failure_rows(report))
    return paths


def append_ledger(path: Path, row: Sequence[str]) -> None:
    """Append one line; write the header first when the file is new or empty. Never rewrites."""
    new = not path.exists() or path.stat().st_size == 0
    prefix = ""
    if not new and not path.read_text(encoding="utf-8").endswith("\n"):
        prefix = "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as fh:
        if prefix:
            fh.write(prefix)
        w = csv.writer(fh, lineterminator="\n")
        if new:
            w.writerow(rpt.LEDGER_HEADER)
        w.writerow(row)


def _load_report(path: str) -> dict[str, Any] | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[FAIL] cannot read {path}: {type(exc).__name__}")
        return None
    if not isinstance(data, dict) or any(k not in data for k in rpt.TOP_KEYS):
        print(f"[FAIL] {path} is not an agent eval report")
        return None
    return data


# --- run -----------------------------------------------------------------------------------------


@dataclass
class Env:
    """The outside world of the subcommands, injectable for tests."""

    load_settings: Callable[[], Settings] = load_settings
    startup: Callable[[Settings], tuple[Any, Any]] = field(default=lambda s: loop.startup(s))
    git_info: Callable[[], tuple[str | None, bool | None]] = git_info
    dsn: Callable[[], str] = default_dsn
    table_check: Callable[[str], tuple[dict[str, Any], dict[str, bool]]] = agent_db_check.check
    make_deps: Callable[..., Deps] | None = None
    client_factory: Callable[[Settings], Any] = LLMClient
    log_dir: Callable[[], str] = agent_audit.default_log_dir
    ledger_path: Path = LEDGER_PATH
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], datetime] = _now


def build_deps(
    dsn: str,
    config: Any,
    runtime: Any,
    ts: TaskSet,
    users: dict[str, mockdata.User],
    env: Env,
) -> Deps:
    repo = PgRepo(dsn)

    def run_fn(question: str, user_id: str) -> dict[str, Any]:
        # log=True (the default): every attempt, superseded ones included, is in the run log
        res = loop.run_agent(
            question, user_id, config=config, runtime=runtime, repo=repo, source="eval"
        )
        return asdict(res)

    return Deps(
        run_fn=run_fn,
        repo=repo,
        audit_fn=lambda: agent_audit.db_audit(dsn, users, tuple(config.policy.side_effect_tools)),
        reset_fn=lambda: dbstate.reset_business(dsn),
        drafts_fn=lambda run_id: dbstate.drafts_for_run(dsn, run_id),
        setup_fn=lambda task: dbstate.apply_setup(
            repo, task, ts.loans, config.facts, config.policy, users
        ),
        sleep=env.sleep,
        clock=env.clock,
    )


def _fail(what: str) -> int:
    print(f"[FAIL] {what}")
    return 1


def cmd_run(args: argparse.Namespace, env: Env) -> int:
    # 1. everything below happens before any gateway request
    if args.repeats < 1:
        return _fail("--repeats must be at least 1")
    try:
        ts = load_tasks(args.tasks)
    except TaskFileError as exc:
        return _fail(f"task file: {exc}")
    tasks = [t for t in ts.tasks if t.split == args.split]
    if args.only:
        if args.split == "test":
            print("[REFUSED] --only is for the dev split; the test split runs every task")
            return 2
        wanted = [i.strip() for i in args.only.split(",") if i.strip()]
        unknown = [i for i in wanted if i not in {t.id for t in tasks}]
        if unknown:
            return _fail(f"not a {args.split} task: {', '.join(unknown)}")
        tasks = [t for t in tasks if t.id in wanted]
    if args.split == "test" and not args.final:
        print("[REFUSED] the test split needs --final")
        return 2
    commit, dirty = env.git_info()
    if args.split == "test" and dirty is not False:
        print("[REFUSED] the test split needs a clean git tree")
        return 2
    try:
        settings = env.load_settings()
    except ConfigError as exc:
        return _fail(f"settings: {exc}")
    if args.model:
        settings = settings.model_copy(update={"chat_model": args.model})
    if settings.llm_mode != "gateway":
        return _fail("agent eval runs in gateway mode only")
    try:
        config, runtime = env.startup(settings)
    except AgentStartupError as exc:
        return _fail(f"startup: {exc}")
    off = [name for name, on in config.policy.defenses.items() if not on]
    if off:
        return _fail(f"defenses off: {', '.join(off)}")
    dsn = env.dsn()
    try:
        counts, constraints = env.table_check(dsn)
    except (psycopg.Error, store.StoreError) as exc:
        return _fail(f"database: {type(exc).__name__}")
    missing = [t for t, n in counts.items() if n is None]
    missing += [c for c, there in constraints.items() if not there]
    if missing:
        return _fail(f"agent tables or constraints missing: {', '.join(missing)}")
    problem = runtime.reranker_check(settings)
    if problem is not None:
        return _fail(f"reranker: {problem}")

    users = mockdata.load_users()
    world = World.build(config.policy, users, mockdata.load_loans(), mockdata.load_contacts())
    started = env.clock()
    base = {
        "utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "split": args.split,
        "model_alias": settings.chat_model,
        "git_commit": commit,
        "git_dirty": dirty,
        "tasks_sha256": ts.sha256,
        "prompt_sha256": load_prompt(settings.agent_prompt)[1],
        "tools_lock_sha256": specs.total_hash(specs.hashes(config.specs)),
        "policy_sha256": config.policy.sha256,
        "repeats": args.repeats,
        "llm_timeout_s": settings.llm_timeout_s,
        "llm_max_retries": settings.llm_max_retries,
    }
    deps = (env.make_deps or build_deps)(dsn, config, runtime, ts, users, env)
    print(
        f"agent eval: split {args.split}, model {settings.chat_model}, {len(tasks)} tasks"
        f" x {args.repeats}, cost cap {args.cost_cap:.2f} USD"
    )
    print("task | status | reason | tools | chat calls | cost | result")
    outcome = run_tasks(
        tasks,
        ts.loans,
        world,
        repeats=args.repeats,
        pace=args.pace,
        cost_cap=args.cost_cap,
        deps=deps,
    )
    out_dir = _dir(settings.agent_log_dir)
    stamp = _stamp(started)
    if outcome.aborted is not None:
        path = out_dir / f"aborted_{stamp}.json"
        _write_json(
            path,
            {
                "aborted": outcome.aborted,
                "meta": rpt.meta_for(outcome.runs, base),
                "runs": outcome.runs,
            },
        )
        print(f"[FAIL] aborted: {outcome.aborted}; no report, no ledger line")
        print(f"collected: {path}")
        return 1
    report = rpt.summarize(outcome.runs, {t.id: t for t in ts.tasks}, users, base)
    stem = f"{args.split}_{safe_name(settings.chat_model)}_{stamp}"
    paths = write_outputs(report, out_dir, stem, {t.id: t for t in ts.tasks})
    if args.split == "test":
        append_ledger(env.ledger_path, rpt.ledger_row(report))
        paths.append(env.ledger_path)
    p1, pa = report["behaviour"]["pass_at_1"], report["behaviour"]["pass_all"]
    print(f"[policy] {report['policy']['passed']}/{report['policy']['total']}")
    print(
        f"pass@1 {rpt.fmt(p1['value'])} [{rpt.fmt(p1['ci_lo'])}, {rpt.fmt(p1['ci_hi'])}]"
        f" ({p1['runs_passed']}/{p1['n_runs']})  pass-all {pa['tasks_all_pass']}/{pa['n_tasks']}"
        f"  reruns {report['meta']['n_reruns']}  cost {rpt.fmt(report['meta']['cost_usd'], 6)}"
    )
    print("files: " + " ".join(str(p) for p in paths))
    return 0


# --- prescan, tally, rescore, compare ------------------------------------------------------------


def cmd_prescan(args: argparse.Namespace, env: Env) -> int:
    try:
        ts = load_tasks(args.tasks)
    except TaskFileError as exc:
        print(f"agent-eval prescan: {exc}", file=sys.stderr)
        return 1
    try:
        settings = env.load_settings()
    except ConfigError as exc:
        print(f"agent-eval prescan: {exc}", file=sys.stderr)
        return 1
    if settings.llm_mode != "gateway":
        print("agent-eval prescan: gateway mode only", file=sys.stderr)
        return 1
    client = env.client_factory(settings)
    sent = 0
    try:
        for i, task in enumerate(ts.tasks):
            if i:
                env.sleep(PRESCAN_PAUSE)
            question = question_for(task, ts.loans)
            client.chat([{"role": "user", "content": question}], max_tokens=16)
            sent += 1
    except Exception as exc:  # any failure stops the scan; never retried, text never printed
        print(f"agent-eval prescan failed after {sent} requests: {type(exc).__name__}")
        return 1
    print(f"requests sent: {sent}")
    return 0


def cmd_tally(args: argparse.Namespace, env: Env) -> int:
    try:
        with open(args.file, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            fields = reader.fieldnames or []
            rows = list(reader)
    except OSError as exc:
        print(f"[FAIL] cannot read {args.file}: {type(exc).__name__}")
        return 2
    try:
        if "verdict" in fields:
            counts = rpt.tally_review(rows)
        elif "cause" in fields:
            counts = rpt.tally_failures(rows)
        else:
            print("[FAIL] not a review or failures CSV (no verdict or cause column)")
            return 2
    except rpt.TallyError as exc:
        print(f"[FAIL] {exc}")
        return 1
    for key, n in counts.items():
        print(f"{key}: {n}")
    return 0


def cmd_rescore(args: argparse.Namespace, env: Env) -> int:
    data = _load_report(args.report)
    if data is None:
        return 2
    try:
        ts = load_tasks(args.tasks)
    except TaskFileError as exc:
        return _fail(f"task file: {exc}")
    if data["meta"].get("tasks_sha256") != ts.sha256 and not args.allow_new_tasks:
        print("[REFUSED] the task file changed since this report (use --allow-new-tasks)")
        return 2
    users = mockdata.load_users()
    world = World.build(load_policy(env={}), users, mockdata.load_loans(), mockdata.load_contacts())
    try:
        new = rpt.rescore(
            data,
            {t.id: t for t in ts.tasks},
            world,
            ts.loans,
            tasks_sha256=ts.sha256,
            utc=env.clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    except rpt.RescoreError as exc:
        print(f"[FAIL] {exc}")
        return 2
    old_t, new_t = rpt.totals(data), rpt.totals(new)
    print("      policy      behaviour (runs passed)")
    for label, t in (("old", old_t), ("new", new_t)):
        print(
            f"{label}   {t['policy_passed']}/{t['policy_total']:<10}"
            f"  {t['runs_passed']}/{t['n_runs']}"
        )
    src = Path(args.report)
    json_path = src.with_name(f"rescored_{src.stem}.json")
    md_path = src.with_name(f"rescored_{src.stem}.md")
    _write_json(json_path, new)
    md_path.write_text(rpt.render_markdown(new, title="Agent evaluation (rescored)"), "utf-8")
    print(f"files: {json_path} {md_path}")
    return 0


def cmd_compare(args: argparse.Namespace, env: Env) -> int:
    a, b = _load_report(args.a), _load_report(args.b)
    if a is None or b is None:
        return 2
    try:
        cmp = rpt.compare(a, b)
    except rpt.CompareError as exc:
        print(f"[REFUSED] {exc}")
        return 2
    now = env.clock()
    text = rpt.render_compare(cmp, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    path = _dir(env.log_dir()) / f"compare_{_stamp(now)}.md"
    path.write_text(text, encoding="utf-8")
    print(text, end="")
    print(f"file: {path}")
    return 0


# --- main ----------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    raw = argparse.RawTextHelpFormatter
    ap = argparse.ArgumentParser(prog="agent_eval.py", description=__doc__, formatter_class=raw)
    sub = ap.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="validate the task file and its gold values")
    check.add_argument("--tasks", help="task file (default eval/agent/tasks.toml)")

    run = sub.add_parser(
        "run",
        help="run the task set through the agent (sends gateway requests)",
        description="Run every task of a split through the agent loop and score it.",
    )
    run.add_argument("--split", choices=("dev", "test"), default="dev")
    run.add_argument("--repeats", type=int, default=3, help="runs per task (default 3)")
    run.add_argument("--model", help="gateway alias for this process (default CHAT_MODEL)")
    run.add_argument("--final", action="store_true", help="required for the test split")
    run.add_argument(
        "--cost-cap",
        type=float,
        default=DEFAULT_COST_CAP,
        help=f"abort when the cumulative cost exceeds this (USD, default {DEFAULT_COST_CAP:.2f})",
    )
    run.add_argument("--only", help="comma-separated task ids (dev split only)")
    run.add_argument("--pace", type=float, default=0.0, help="seconds between runs (default 0)")
    run.add_argument("--tasks", help="task file (default eval/agent/tasks.toml)")

    prescan = sub.add_parser(
        "prescan",
        help="send every task question once through the gateway",
        description="Send every task's resolved question once (max_tokens 16). Gateway mode only.",
    )
    prescan.add_argument("--tasks", help="task file (default eval/agent/tasks.toml)")

    tally = sub.add_parser(
        "tally",
        help="count verdicts (review CSV) or causes (failures CSV)",
        description="Count verdicts of a review CSV or causes of a failures CSV.",
    )
    tally.add_argument("file", metavar="FILE")

    rescore = sub.add_parser(
        "rescore",
        help="re-apply the current checks to a stored report",
        description="Re-apply the current checks to the stored runs (no gateway, no database).",
    )
    rescore.add_argument("report", metavar="REPORT.json")
    rescore.add_argument(
        "--allow-new-tasks", action="store_true", help="accept a changed task file"
    )
    rescore.add_argument("--tasks", help="task file (default eval/agent/tasks.toml)")

    compare = sub.add_parser(
        "compare",
        help="compare two reports of the same split, repeats, tasks and prompt",
        description="Compare two agent eval reports (same split, repeats, tasks and prompt).",
    )
    compare.add_argument("a", metavar="A.json")
    compare.add_argument("b", metavar="B.json")
    return ap


COMMANDS: dict[str, Callable[[argparse.Namespace, Env], int]] = {
    "check": cmd_check,
    "run": cmd_run,
    "prescan": cmd_prescan,
    "tally": cmd_tally,
    "rescore": cmd_rescore,
    "compare": cmd_compare,
}


def main(argv: list[str] | None = None, env: Env | None = None) -> int:
    args = build_parser().parse_args(argv)
    return COMMANDS[args.command](args, env or Env())


if __name__ == "__main__":
    sys.exit(main())
