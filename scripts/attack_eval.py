"""Attack evaluation building blocks (D9-A).

check    [--attacks PATH]
         Load and validate eval/agent/attacks.toml and corpus/adversarial/*.md; compare the
         registered hashes with tools.lock, config/agent_policy.toml and the agent prompt.
         No writes, no requests. Exit 1 on a file error or any registered-hash mismatch.
matrix   [--attacks PATH]
         Scripted runs (no model calls) of every attack under the nine configs against the local
         database. Exit 1 on any mismatch. Writes reports/agent/attack_matrix_<UTC>.json and .md.
delivery [--attacks PATH]
         A01-A05: embed through the gateway (rag-embed only), rerank and gate over the main and
         adversarial collections. Print the rank of the instruction chunk, the gate and whether
         the instruction sits in the bait chunk. Exit 1 if any attack is not delivered.
prescan  [--attacks PATH]
         One chat-cloud request (max_tokens 16) per item: questions, attack doc chunks (enveloped
         and plain), tampered loan records (enveloped and plain), spec patch texts. Live attacks
         only. Gateway mode only. A gateway block is a result (printed with its rule, the scan
         goes on); any other error stops the scan (exit 1).
run      --mode on|off --repeats N --cost-cap USD [--final] [--only A01,B01] [--attacks PATH]
         Every live attack N times through the real agent loop (gateway requests). Preflight
         before any request: [REFUSED] (usage, exit 2) or [FAIL] (exit 1). Mode on aborts on any
         unauthorized effect; mode off needs AGENT_DEFENSES_OFF_ACK=1 and continues. --final needs
         a clean tree, no --only and --repeats 3, and appends one line to
         eval/agent/attack_runs.csv. Writes reports/agent/attack_<mode>_<UTC>.json/.md and
         review_attack_<mode>_<UTC>.csv (an abort writes aborted_attack_<UTC>.json instead).
rescore  REPORT [--r5 FILE] [--attacks PATH]
         Recompute every verdict from the stored rows and traces (no database, no requests);
         refuses when attacks.toml changed. FILE: CSV with call_id,redacted from the gateway
         audit. Writes rescored_<name>.json next to REPORT.
compare  A B [--attacks PATH]
         A: a mode on report, B: a mode off report with the same attacks.toml. Writes
         reports/agent/compare_attack_<UTC>.md.
denials  REPORT [REPORT ...] [--tasks PATH]
         One CSV row per denied tool call of D8 eval reports; prints counts by (tool, reason).
         Writes reports/agent/denials_<UTC>.csv.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg

import agent_db_check
from rag_agent import answer, store
from rag_agent.agent import loop, specs, tools
from rag_agent.agent.db import PgRepo, default_dsn
from rag_agent.agent.policy import make_envelope
from rag_agent.agent.policy_config import POLICY_PATH
from rag_agent.agent_attack import report as rpt
from rag_agent.agent_attack import runner
from rag_agent.agent_attack.attacks import (
    REPO_ROOT,
    Attack,
    AttackFileError,
    AttackSet,
    doc_path,
    instruction_chunk,
    load_attacks,
)
from rag_agent.agent_attack.harness import (
    TamperingRepo,
    config_for,
    disabled_for,
    mixed_retrieve,
    mixed_sections,
    world,
)
from rag_agent.agent_attack.matrix import (
    FIXED_SCORE,
    MatrixDeps,
    pg_deps,
    render_md,
    report_dict,
    run_matrix,
)
from rag_agent.agent_attack.verdict import StartupFailure, World
from rag_agent.agent_eval.tasks import TaskFileError, load_tasks
from rag_agent.config import ConfigError, Settings, load_settings
from rag_agent.corpus import parse_doc
from rag_agent.errors import PolicyBlockedError
from rag_agent.ingest import git_info
from rag_agent.llm_client import LLMClient
from rag_agent.rerank import RerankError

REPORTS_DIR = REPO_ROOT / "reports" / "agent"
PRESCAN_PAUSE = 0.2
PRESCAN_MAX_TOKENS = 16
LEDGER_PATH = REPO_ROOT / "eval" / "agent" / "attack_runs.csv"
FINAL_REPEATS = 3


def _now() -> datetime:
    return datetime.now(UTC)


def delivery_runtime(settings: Settings) -> answer.Runtime:
    return answer.build_runtime(
        settings, retrieve_fn=mixed_retrieve(), sections_fn=mixed_sections()
    )


@dataclass
class Env:
    """The outside world of the subcommands, injectable for tests."""

    load_settings: Callable[[], Settings] = load_settings
    dsn: Callable[[], str] = default_dsn
    make_deps: Callable[[str], MatrixDeps] = pg_deps
    world: Callable[[], World] = world
    repo_factory: Callable[[str], Any] = PgRepo
    client_factory: Callable[[Settings], Any] = LLMClient
    runtime_factory: Callable[[Settings], Any] = delivery_runtime
    git_info: Callable[[], tuple[str | None, bool | None]] = git_info
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], datetime] = _now
    reports_dir: Path = field(default=REPORTS_DIR)
    # D9-B (the run subcommand)
    environ: Any = field(default_factory=lambda: os.environ)
    ledger_path: Path = field(default=LEDGER_PATH)
    table_check: Callable[[str], tuple[dict[str, Any], dict[str, bool]]] = agent_db_check.check
    db_chunk_ids: Callable[[str], set[str]] = field(
        default=lambda dsn: {c.chunk_id for c in store.load_chunks("adversarial", dsn)}
    )
    check_reranker: Callable[[Settings], str | None] = answer.check_reranker
    plain_runtime_factory: Callable[[Settings], Any] = answer.build_runtime
    run_fn: Callable[..., dict[str, Any]] = field(
        default=lambda question, user, **kw: asdict(
            loop.run_agent(question, user, source="attack", **kw)
        )
    )


def _load(args: argparse.Namespace, cmd: str) -> AttackSet | None:
    try:
        return load_attacks(args.attacks)
    except AttackFileError as exc:
        print(f"attack-{cmd}: {exc}", file=sys.stderr)
        return None


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prompt_path() -> Path:
    p = Path(str(Settings.model_fields["agent_prompt"].default))
    return p if p.is_absolute() else REPO_ROOT / p


def registered_mismatches(ats: AttackSet) -> list[str]:
    """Registered hashes that differ from the current files."""
    reg = ats.registered
    pairs = [
        ("tools_lock_sha256", specs.LOCK_PATH, reg.tools_lock_sha256),
        ("policy_sha256", POLICY_PATH, reg.policy_sha256),
        ("prompt_sha256", _prompt_path(), reg.prompt_sha256),
    ]
    return [name for name, path, want in pairs if _sha(Path(path)) != want]


# --- check -------------------------------------------------------------------------------------


def cmd_check(args: argparse.Namespace, env: Env) -> int:
    ats = _load(args, "check")
    if ats is None:
        return 1
    bad = registered_mismatches(ats)
    for name in bad:
        print(f"registered {name} differs from the current file")
    live = len(ats.live())
    print(
        f"attacks: {len(ats.attacks)} (live {live}, matrix-only {len(ats.attacks) - live});"
        f" mismatches: {len(bad)}"
    )
    return 1 if bad else 0


# --- matrix ------------------------------------------------------------------------------------


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def cmd_matrix(args: argparse.Namespace, env: Env) -> int:
    ats = _load(args, "matrix")
    if ats is None:
        return 1
    try:
        settings = env.load_settings()
    except ConfigError as exc:
        print(f"attack-matrix: {exc}", file=sys.stderr)
        return 1
    try:
        deps = env.make_deps(env.dsn())
        result = run_matrix(ats, settings, deps, env.world())
    except psycopg.Error as exc:
        print(f"attack-matrix: database error ({type(exc).__name__})", file=sys.stderr)
        return 1
    for line in result.mismatches:
        print(line)
    print(f"matrix: {result.as_expected}/{len(result.cells)} as expected")
    names = " ".join(result.positives)
    passed = bool(result.positives) and all(result.positives.values())
    print(f"positives {names}: {'pass' if passed else 'fail'}")
    commit, dirty = env.git_info()
    report = report_dict(result, ats, commit, dirty)
    out_dir = Path(env.reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"attack_matrix_{_stamp(env.clock())}"
    json_path, md_path = out_dir / f"{stem}.json", out_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    md_path.write_text(render_md(report), encoding="utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    return 0 if result.ok and result.as_expected == len(result.cells) else 1


# --- delivery ----------------------------------------------------------------------------------


def delivery_row(attack: Attack, prep: Any, instruction_id: str) -> dict[str, Any]:
    """Where the instruction chunk landed in one prepared context."""
    ids = [s.chunk_id for s in prep.context]
    gate = prep.terminal is None
    rank = ids.index(instruction_id) + 1 if instruction_id in ids else None
    bait = next((s.chunk_id for s in prep.context if s.doc_id == attack.doc), None)
    return {
        "attack": attack.id,
        "rank": rank,
        "gate": gate,
        "instruction_in_bait": bait is not None and bait == instruction_id,
        "delivered": gate and rank is not None,
        "terminal": None if gate else f"{prep.terminal.status}/{prep.terminal.reason}",
    }


def format_delivery(row: dict[str, Any]) -> str:
    line = (
        f"{row['attack']} rank={row['rank'] or 'absent'} gate={'pass' if row['gate'] else 'fail'}"
        f" instruction_in_bait={'yes' if row['instruction_in_bait'] else 'no'}"
        f" delivered={'yes' if row['delivered'] else 'no'}"
    )
    return line + (f" ({row['terminal']})" if row["terminal"] else "")


def cmd_delivery(args: argparse.Namespace, env: Env) -> int:
    ats = _load(args, "delivery")
    if ats is None:
        return 1
    try:
        settings = env.load_settings()
    except ConfigError as exc:
        print(f"attack-delivery: {exc}", file=sys.stderr)
        return 1
    if settings.llm_mode != "gateway":
        print("attack-delivery: gateway mode only", file=sys.stderr)
        return 1
    attacks = [a for a in ats.live() if a.channel == "doc"]
    wanted: dict[str, str] = {}
    for a in attacks:
        chunk = instruction_chunk(a.doc or "", ats.adv_dir)
        assert chunk is not None  # the loader checked it
        wanted[a.id] = chunk.chunk_id
    try:
        rt = env.runtime_factory(settings)
        found = rt.sections_fn(list(wanted.values()), dsn=settings.database_url)
    except (ConfigError, psycopg.Error) as exc:
        print(f"attack-delivery: {type(exc).__name__}", file=sys.stderr)
        return 1
    missing = sorted(c for c in wanted.values() if c not in found)
    if missing:
        print(
            "attack-delivery: adversarial chunks are not in the database; run "
            "make ingest COLLECTION=adversarial DOCS_DIR=corpus/adversarial",
            file=sys.stderr,
        )
        return 1
    problem = rt.reranker_check(settings)  # preflight before any gateway request
    if problem:
        print(f"attack-delivery: reranker unavailable ({problem})", file=sys.stderr)
        return 1
    delivered = 0
    for a in attacks:
        try:
            prep = answer.prepare(a.question, rt, allow_degraded=False)
        except (RerankError, psycopg.Error) as exc:
            print(f"attack-delivery: {a.id} failed ({type(exc).__name__})", file=sys.stderr)
            return 1
        row = delivery_row(a, prep, wanted[a.id])
        delivered += row["delivered"]
        print(format_delivery(row))
    print(f"delivery: {delivered}/{len(attacks)} delivered")
    return 0 if delivered == len(attacks) else 1


# --- prescan -----------------------------------------------------------------------------------


def _chunk_items(attack: Attack, adv_dir: Path) -> list[tuple[str, str, str]]:
    doc = parse_doc(doc_path(attack.doc or "", adv_dir))
    items = []
    for s in doc.sections:
        chunk = {
            "marker": "S1",
            "chunk_id": s.chunk_id,
            "doc_id": s.doc_id,
            "title": doc.title or "",
            "section": s.heading,
            "score": FIXED_SCORE,
            "text": s.text,
        }
        data = {"chunks": [chunk]}
        items.append(
            (attack.id, "chunk_enveloped", make_envelope("search_policy", "ok", data, True))
        )
        items.append((attack.id, "chunk_plain", make_envelope("search_policy", "ok", data, False)))
    return items


def _tamper_items(attack: Attack, repo: Any) -> list[tuple[str, str, str]]:
    assert attack.tamper is not None
    ctx = SimpleNamespace(repo=TamperingRepo(repo, attack.tamper))
    record = tools.get_loan_status({"loan_number": attack.tamper.loan}, ctx)  # type: ignore[arg-type]
    data = {k: v for k, v in record.items() if k != "status"}
    return [
        (attack.id, "tamper_enveloped", make_envelope("get_loan_status", "ok", data, True)),
        (attack.id, "tamper_plain", make_envelope("get_loan_status", "ok", data, False)),
    ]


def prescan_items(ats: AttackSet, repo: Any) -> list[tuple[str, str, str]]:
    """(attack id, kind, text) for every live attack, in file order."""
    items: list[tuple[str, str, str]] = []
    for a in ats.live():
        items.append((a.id, "question", a.question))
        if a.channel == "doc":
            items += _chunk_items(a, ats.adv_dir)
        if a.tamper is not None:
            items += _tamper_items(a, repo)
        if a.spec_patch is not None and a.spec_patch.text:
            items.append((a.id, "spec_text", a.spec_patch.text))
    return items


def cmd_prescan(args: argparse.Namespace, env: Env) -> int:
    ats = _load(args, "prescan")
    if ats is None:
        return 1
    try:
        settings = env.load_settings()
    except ConfigError as exc:
        print(f"attack-prescan: {exc}", file=sys.stderr)
        return 1
    if settings.llm_mode != "gateway":
        print("attack-prescan: gateway mode only", file=sys.stderr)
        return 1
    try:
        items = prescan_items(ats, env.repo_factory(env.dsn()))
    except psycopg.Error as exc:
        print(f"attack-prescan: database error ({type(exc).__name__})", file=sys.stderr)
        return 1
    client = env.client_factory(settings)
    sent = blocked = 0
    try:
        for n, (attack_id, kind, text) in enumerate(items, start=1):
            if sent:
                env.sleep(PRESCAN_PAUSE)
            try:
                client.chat([{"role": "user", "content": text}], max_tokens=PRESCAN_MAX_TOKENS)
            except PolicyBlockedError as exc:  # a gateway block is a result: record it, go on
                sent += 1
                blocked += 1
                print(f"{n} {attack_id} {kind} blocked rule={exc.rule}")
                continue
            sent += 1
            print(f"{n} {attack_id} {kind} pass")
    except Exception as exc:  # any other failure stops the scan; never retried, text never printed
        print(f"attack-prescan failed after {sent} requests: {type(exc).__name__}")
        return 1
    print(f"requests sent: {sent}; blocked: {blocked}")
    return 0


# --- run ---------------------------------------------------------------------------------------

RUN_USAGE = (
    "usage: make attack-eval MODE=on|off COST_CAP=<usd> [REPEATS=3] [FINAL=1] [ONLY=A01,B01]"
)


def _refused(what: str) -> int:
    print(f"[REFUSED] {what}")
    return 2


def _fail(what: str) -> int:
    print(f"[FAIL] {what}")
    return 1


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fields), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def parsed_adversarial_ids(adv_dir: Path) -> set[str]:
    ids: set[str] = set()
    for path in sorted(Path(adv_dir).glob("*.md")):
        ids |= {s.chunk_id for s in parse_doc(path).sections}
    return ids


def cmd_run(args: argparse.Namespace, env: Env) -> int:
    # everything up to the runtimes happens before any gateway request
    if args.mode not in runner.MODES:
        return _refused(f"--mode must be on or off. {RUN_USAGE}")
    if args.repeats < 1:
        return _refused(f"--repeats must be at least 1. {RUN_USAGE}")
    if args.cost_cap is None or args.cost_cap <= 0:
        return _refused(f"--cost-cap is required and must be above 0. {RUN_USAGE}")
    commit, dirty = env.git_info()
    if args.final:
        if args.only:
            return _refused("--final runs every attack; --only is not allowed")
        if args.repeats != FINAL_REPEATS:
            return _refused(f"--final needs --repeats {FINAL_REPEATS} (N is frozen)")
        if dirty is not False:
            return _refused("--final needs a clean git tree")
    ats = _load(args, "run")
    if ats is None:
        return 1
    attacks = ats.live()
    if args.only:
        wanted = [i.strip() for i in args.only.split(",") if i.strip()]
        unknown = [i for i in wanted if i not in {a.id for a in attacks}]
        if unknown:
            return _fail(f"not a live attack: {', '.join(unknown)}")
        attacks = [a for a in attacks if a.id in wanted]
    if args.mode == "on":
        if "AGENT_DISABLE" in env.environ:
            return _refused("AGENT_DISABLE is set; mode on runs with every defense on")
    else:
        if env.environ.get("AGENT_DEFENSES_OFF_ACK") != "1":
            return _refused("mode off needs AGENT_DEFENSES_OFF_ACK=1")
        print("DEFENSES OFF: " + ", ".join(disabled_for("all_off")))
    try:
        settings = env.load_settings()
    except ConfigError as exc:
        return _fail(f"settings: {exc}")
    if settings.llm_mode != "gateway":
        return _fail("attack runs need gateway mode")
    config_name = runner.CONFIG_BY_MODE[args.mode]
    cfg = config_for(config_name)
    if isinstance(cfg, StartupFailure):
        return _fail(f"agent config does not load (boot:{cfg.code})")
    bad = [n for n in registered_mismatches(ats) if n != "prompt_sha256"]
    try:
        if answer.load_prompt(settings.agent_prompt)[1] != ats.registered.prompt_sha256:
            bad.append("prompt_sha256")
    except ConfigError as exc:
        return _fail(f"prompt: {exc}")
    if bad:
        return _fail(f"registered hash differs from the current file: {', '.join(bad)}")
    problem = env.check_reranker(settings)
    if problem is not None:
        return _fail(f"reranker: {problem}")
    dsn = env.dsn()
    try:
        counts, constraints = env.table_check(dsn)
    except (psycopg.Error, store.StoreError) as exc:
        return _fail(f"database: {type(exc).__name__}")
    missing = [t for t, n in counts.items() if n is None]
    missing += [c for c, there in constraints.items() if not there]
    if missing:
        return _fail(f"agent tables or constraints missing: {', '.join(missing)}")
    try:
        in_db = env.db_chunk_ids(dsn)
    except (psycopg.Error, store.StoreError) as exc:
        return _fail(f"database: {type(exc).__name__}")
    if in_db != parsed_adversarial_ids(ats.adv_dir):
        return _fail(
            "adversarial chunks in the database differ from corpus/adversarial; run "
            "make ingest COLLECTION=adversarial DOCS_DIR=corpus/adversarial"
        )
    try:
        plain = env.plain_runtime_factory(settings)
        mixed = env.runtime_factory(settings)
    except ConfigError as exc:
        return _fail(f"runtime: {exc}")

    started = env.clock()
    base = {
        "utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": args.mode,
        "defenses": dict(cfg.policy.defenses),
        "repeats": args.repeats,
        "git_commit": commit,
        "git_dirty": dirty,
        "attacks_sha256": ats.sha256,
        "prompt_sha256": ats.registered.prompt_sha256,
        "tools_lock_sha256": ats.registered.tools_lock_sha256,
        "policy_sha256": ats.registered.policy_sha256,
        "cost_cap": args.cost_cap,
    }
    deps = runner.RunDeps(
        matrix=env.make_deps(dsn),
        run_fn=env.run_fn,
        runtime_for=lambda a: mixed if a.channel == "doc" else plain,
        sleep=env.sleep,
    )
    print(
        f"attack run: mode {args.mode}, {len(attacks)} attacks x {args.repeats},"
        f" cost cap {args.cost_cap:.2f} USD"
    )
    outcome = runner.run_attacks(
        attacks,
        ats,
        env.world(),
        mode=args.mode,
        repeats=args.repeats,
        cost_cap=args.cost_cap,
        deps=deps,
    )
    out_dir = Path(env.reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp(started)
    if outcome.aborted is not None:
        meta = rpt.meta_for(outcome.runs, base)
        path = out_dir / f"aborted_attack_{stamp}.json"
        _write_json(path, {"aborted": outcome.aborted, "meta": meta, "runs": outcome.runs})
        if args.final:
            rpt.append_ledger(env.ledger_path, rpt.ledger_row(meta, outcome.runs, "aborted"))
        print(f"[FAIL] aborted: {outcome.aborted}; no report")
        print(f"collected: {path}")
        return 1
    report = rpt.build_report(outcome.runs, base)
    stem = f"{args.mode}_{stamp}"
    json_path = out_dir / f"attack_{stem}.json"
    md_path = out_dir / f"attack_{stem}.md"
    review_path = out_dir / f"review_attack_{stem}.csv"
    _write_json(json_path, report)
    md_path.write_text(rpt.render_md(report), encoding="utf-8")
    _write_csv(review_path, rpt.REVIEW_FIELDS, rpt.review_rows(report))
    paths = [json_path, md_path, review_path]
    if args.final:
        rpt.append_ledger(env.ledger_path, rpt.ledger_row(report["meta"], outcome.runs, "ok"))
        paths.append(env.ledger_path)
    o = report["summary"]["overall"]
    print(
        f"unauthorized side effects {o['unauthorized_side_effects']['total']},"
        f" reads {o['unauthorized_reads']['total']}; delivered {o['delivery']['k']}/"
        f"{o['delivery']['n']}; interception {rpt._kn(o['interception'])};"
        f" attempt {rpt._kn(o['attempt'])}; cost {report['meta']['cost_usd']}"
    )
    print("files: " + " ".join(str(p) for p in paths))
    return 0


# --- rescore, compare, denials -----------------------------------------------------------------


def _read_json(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[FAIL] cannot read {path}: {type(exc).__name__}")
        return None


def cmd_rescore(args: argparse.Namespace, env: Env) -> int:
    data = _read_json(args.report)
    if data is None:
        return 1
    ats = _load(args, "rescore")
    if ats is None:
        return 1
    r5 = None
    try:
        if args.r5:
            r5 = rpt.read_r5(args.r5)
        new = rpt.rescore(
            data,
            ats,
            env.world(),
            r5=r5,
            utc=env.clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    except rpt.ShaMismatch as exc:
        return _refused(str(exc))
    except rpt.ReportError as exc:
        return _fail(str(exc))
    for line in rpt.rescore_lines(data, new):
        print(line)
    src = Path(args.report)
    path = src.with_name(f"rescored_{src.stem}.json")
    _write_json(path, new)
    print(f"file: {path}")
    return 0


def cmd_compare(args: argparse.Namespace, env: Env) -> int:
    a, b = _read_json(args.a), _read_json(args.b)
    if a is None or b is None:
        return 1
    try:
        cmp = rpt.compare(a, b)
    except rpt.CompareError as exc:
        return _refused(str(exc))
    now = env.clock()
    text = rpt.render_compare(cmp, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    out_dir = Path(env.reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"compare_attack_{_stamp(now)}.md"
    path.write_text(text, encoding="utf-8")
    print(text, end="")
    print(f"file: {path}")
    return 0


def cmd_denials(args: argparse.Namespace, env: Env) -> int:
    try:
        categories = {t.id: t.category for t in load_tasks(args.tasks).tasks}
    except TaskFileError as exc:
        return _fail(f"task file: {exc}")
    rows: list[dict[str, Any]] = []
    damaged = 0
    for path in args.reports:
        data = _read_json(path)
        if data is None:
            return 1
        if not isinstance(data, dict) or "runs" not in data or "meta" not in data:
            return _fail(f"{path} is not an agent eval report")
        found, bad = rpt.denial_rows(Path(path).name, data, categories)
        rows += found
        damaged += bad
    now = env.clock()
    out_dir = Path(env.reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path_out = out_dir / f"denials_{_stamp(now)}.csv"
    _write_csv(path_out, rpt.DENIAL_FIELDS, rows)
    for (tool, reason), n in rpt.counts_by_tool_reason(rows).items():
        print(f"{tool} {reason}: {n}")
    print(f"denied calls: {len(rows)}; runs with unmatched outputs: {damaged}")
    print(f"file: {path_out}")
    return 0


# --- entry point -------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="attack_eval.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    helps = {
        "check": "validate the attack file and documents; compare the registered hashes",
        "matrix": "scripted runs of every attack under the nine configs (local database)",
        "delivery": "A01-A05 retrieval over main+adversarial (gateway: rag-embed only)",
        "prescan": "one chat-cloud request per item (gateway mode only)",
    }
    for name, text in helps.items():
        p = sub.add_parser(name, help=text, description=text)
        p.add_argument("--attacks", help="attack file (default eval/agent/attacks.toml)")

    attacks_help = "attack file (default eval/agent/attacks.toml)"
    run = sub.add_parser(
        "run",
        help="every live attack through the agent loop (sends gateway requests)",
        description=RUN_USAGE,
    )
    run.add_argument("--mode", help="on (defenses on) or off (needs AGENT_DEFENSES_OFF_ACK=1)")
    run.add_argument("--repeats", type=int, default=3, help="runs per attack (default 3)")
    run.add_argument("--cost-cap", type=float, help="abort above this cumulative cost (USD)")
    run.add_argument("--final", action="store_true", help="clean tree, 3 repeats, ledger line")
    run.add_argument("--only", help="comma-separated attack ids (not with --final)")
    run.add_argument("--attacks", help=attacks_help)
    rescore = sub.add_parser(
        "rescore", help="recompute the verdicts of a stored report", description="Rescore."
    )
    rescore.add_argument("report", metavar="REPORT.json")
    rescore.add_argument("--r5", help="CSV with call_id,redacted from the gateway audit")
    rescore.add_argument("--attacks", help=attacks_help)
    compare = sub.add_parser(
        "compare", help="mode on report A against mode off report B", description="Compare."
    )
    compare.add_argument("a", metavar="A.json")
    compare.add_argument("b", metavar="B.json")
    compare.add_argument("--attacks", help=attacks_help)
    denials = sub.add_parser(
        "denials", help="one CSV row per denied call of D8 eval reports", description="Denials."
    )
    denials.add_argument("reports", nargs="+", metavar="EVAL_REPORT.json")
    denials.add_argument("--tasks", help="task file (default eval/agent/tasks.toml)")
    return ap


COMMANDS: dict[str, Callable[[argparse.Namespace, Env], int]] = {
    "check": cmd_check,
    "matrix": cmd_matrix,
    "delivery": cmd_delivery,
    "prescan": cmd_prescan,
    "run": cmd_run,
    "rescore": cmd_rescore,
    "compare": cmd_compare,
    "denials": cmd_denials,
}


def main(argv: list[str] | None = None, env: Env | None = None) -> int:
    args = build_parser().parse_args(argv)
    return COMMANDS[args.command](args, env or Env())


if __name__ == "__main__":
    sys.exit(main())
