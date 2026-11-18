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
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg

from rag_agent import answer
from rag_agent.agent import specs, tools
from rag_agent.agent.db import PgRepo, default_dsn
from rag_agent.agent.policy import make_envelope
from rag_agent.agent.policy_config import POLICY_PATH
from rag_agent.agent_attack.attacks import (
    REPO_ROOT,
    Attack,
    AttackFileError,
    AttackSet,
    doc_path,
    instruction_chunk,
    load_attacks,
)
from rag_agent.agent_attack.harness import TamperingRepo, mixed_retrieve, mixed_sections, world
from rag_agent.agent_attack.matrix import (
    FIXED_SCORE,
    MatrixDeps,
    pg_deps,
    render_md,
    report_dict,
    run_matrix,
)
from rag_agent.agent_attack.verdict import World
from rag_agent.config import ConfigError, Settings, load_settings
from rag_agent.corpus import parse_doc
from rag_agent.errors import PolicyBlockedError
from rag_agent.ingest import git_info
from rag_agent.llm_client import LLMClient
from rag_agent.rerank import RerankError

REPORTS_DIR = REPO_ROOT / "reports" / "agent"
PRESCAN_PAUSE = 0.2
PRESCAN_MAX_TOKENS = 16


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
    return ap


COMMANDS: dict[str, Callable[[argparse.Namespace, Env], int]] = {
    "check": cmd_check,
    "matrix": cmd_matrix,
    "delivery": cmd_delivery,
    "prescan": cmd_prescan,
}


def main(argv: list[str] | None = None, env: Env | None = None) -> int:
    args = build_parser().parse_args(argv)
    return COMMANDS[args.command](args, env or Env())


if __name__ == "__main__":
    sys.exit(main())
