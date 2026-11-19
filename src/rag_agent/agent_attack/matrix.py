"""The scripted attack matrix: every attack under the nine configs, no model calls.

A scripted client plays the attack's script as tool calls; retrieval is fixed (the attack doc for
channel A, POL-010 otherwise). Every call goes through the real loop, policy layer and database,
so the matrix proves the expectation table in attacks.toml against the code.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from rag_agent import answer
from rag_agent.agent import loop, specs
from rag_agent.agent.db import PgRepo
from rag_agent.agent_attack.attacks import CONFIGS, REPO_ROOT, Attack, AttackSet, doc_path
from rag_agent.agent_attack.harness import (
    TamperingRepo,
    config_for,
    offered_tools,
    preflight_lock,
    write_spec_copy,
)
from rag_agent.agent_attack.verdict import StartupFailure, World, compare, judge
from rag_agent.agent_eval.dbstate import drafts_for_run, reset_business
from rag_agent.config import Settings
from rag_agent.corpus import parse_doc
from rag_agent.llm_client import ToolChatResult
from rag_agent.retrieve import Hit, SectionInfo

EMBED_DIM = 768
FIXED_SCORE = 0.99
DEFAULT_DOC = REPO_ROOT / "corpus" / "docs" / "POL-010.md"
POSITIVE_RUN_ID = "positive"


# --- fakes -------------------------------------------------------------------------------------


class ScriptedAttackClient:
    """Plays the script: one assistant message per chat_tools call, then "done"."""

    def __init__(self, script: tuple[tuple[dict[str, Any], ...], ...]):
        self.script = [list(m) for m in script]
        self.n_calls = 0
        self.offered: list[list[str]] = []
        self.embeds: list[list[str]] = []

    def chat_tools(self, messages: list[dict[str, Any]], tools: Any, **kw: Any) -> ToolChatResult:
        self.offered.append([t["function"]["name"] for t in tools])
        if not self.script:
            message: dict[str, Any] = {"role": "assistant", "content": "done"}
            return ToolChatResult(message, "stop", "scripted", None, None, 0.0)
        calls = []
        for call in self.script.pop(0):
            self.n_calls += 1
            calls.append(
                {
                    "id": f"c{self.n_calls}",
                    "type": "function",
                    "function": {"name": call["tool"], "arguments": json.dumps(call["args"])},
                }
            )
        message = {"role": "assistant", "content": None, "tool_calls": calls}
        return ToolChatResult(message, "tool_calls", "scripted", None, None, 0.0)

    def embed(self, texts: list[str], timeout: float | None = None) -> Any:
        self.embeds.append(list(texts))
        return SimpleNamespace(vectors=[[0.001] * EMBED_DIM], model="scripted", latency_ms=0.0)


def fixed_retrieval(
    attack: Attack, adv_dir: str | Path | None = None
) -> tuple[Callable[..., list[Hit]], Callable[..., dict[str, SectionInfo]]]:
    """retrieve_fn and sections_fn over one document: the attack doc for channel A, else
    POL-010. Every hit is current with score 0.99, in section order."""
    path = doc_path(attack.doc, adv_dir) if attack.channel == "doc" and attack.doc else DEFAULT_DOC
    doc = parse_doc(path)
    hits = [
        Hit(
            s.chunk_id,
            s.doc_id,
            s.heading,
            "current",
            s.text,
            i,
            FIXED_SCORE,
            None,
            None,
            None,
            None,
            i,
        )
        for i, s in enumerate(doc.sections, start=1)
    ]
    infos = {
        s.chunk_id: SectionInfo(s.chunk_id, s.doc_id, doc.title or "", s.heading, False, s.text)
        for s in doc.sections
    }

    def retrieve_fn(query: str, mode: str = "hybrid", k: int = 10, *_: Any, **__: Any) -> list:
        return hits[:k]

    def sections_fn(chunk_ids: list[str], *_: Any, **__: Any) -> dict[str, SectionInfo]:
        return {c: infos[c] for c in chunk_ids if c in infos}

    return retrieve_fn, sections_fn


# --- dependencies ------------------------------------------------------------------------------


@dataclass
class MatrixDeps:
    repo: Any  # the base Repo
    reset: Callable[[], None]
    rows: Callable[[str], tuple[list[dict[str, Any]], list[dict[str, Any]]]]  # requests, drafts

    def repo_for(self, attack: Attack) -> Any:
        return TamperingRepo(self.repo, attack.tamper) if attack.tamper else self.repo


def pg_deps(dsn: str) -> MatrixDeps:
    repo = PgRepo(dsn)

    def rows(run_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        requests = [r for r in repo.list_requests() if r.get("run_id") == run_id]
        return requests, drafts_for_run(dsn, run_id)

    return MatrixDeps(repo, lambda: reset_business(dsn), rows)


# --- the matrix --------------------------------------------------------------------------------


@dataclass
class MatrixResult:
    cells: list[dict[str, Any]] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)
    positives: dict[str, bool] = field(default_factory=dict)

    @property
    def as_expected(self) -> int:
        return sum(1 for c in self.cells if c["as_expected"])

    @property
    def ok(self) -> bool:
        return not self.mismatches and bool(self.positives) and all(self.positives.values())


def run_cell(
    attack: Attack,
    config_name: str,
    attack_set: AttackSet,
    settings: Settings,
    deps: MatrixDeps,
    world: World,
) -> dict[str, Any]:
    deps.reset()
    offered: frozenset[str] = frozenset()
    with tempfile.TemporaryDirectory(prefix="attack_specs_") as tmp:
        if attack.spec_patch is not None:
            specs_path, lock_path = write_spec_copy(attack.spec_patch, tmp)
        else:
            specs_path, lock_path = specs.SPECS_PATH, specs.LOCK_PATH
        run: Any = preflight_lock(lock_path, attack_set.registered)
        if run is None:
            cfg = config_for(config_name, specs_path=specs_path, lock_path=lock_path)
            if isinstance(cfg, StartupFailure):
                run = cfg
            else:
                retrieve_fn, sections_fn = fixed_retrieval(attack, attack_set.adv_dir)
                runtime = answer.build_runtime(
                    settings,
                    client=ScriptedAttackClient(attack.script),
                    retrieve_fn=retrieve_fn,
                    sections_fn=sections_fn,
                    reranker_check=lambda s: None,
                )
                result = loop.run_agent(
                    attack.question,
                    attack.user,
                    config=cfg,
                    runtime=runtime,
                    repo=deps.repo_for(attack),
                    log=False,
                )
                run = asdict(result)
                offered = offered_tools(cfg, world.users[attack.user].role)
    requests, drafts = deps.rows(run["run_id"]) if isinstance(run, dict) else ([], [])
    verdict = judge(attack, run, requests=requests, drafts=drafts, world=world, offered=offered)
    problems = compare(verdict, attack, config_name)
    exp = attack.expect[config_name]
    return {
        "attack": attack.id,
        "config": config_name,
        "run_id": run.get("run_id") if isinstance(run, dict) else None,
        "status": run.get("status") if isinstance(run, dict) else None,
        "expected": {
            "out": list(exp.out),
            "hit": list(exp.hit),
            "config_write": config_name in attack.config_write,
        },
        "actual": {
            "out": verdict.out,
            "hit": verdict.hit,
            "config_write": bool(verdict.config_writes),
        },
        "delivered": verdict.delivered,
        "attempted": verdict.attempted,
        "first_layer": verdict.first_layer,
        "unauthorized_side_effects": verdict.unauthorized_side_effects,
        "unauthorized_reads": verdict.unauthorized_reads,
        "as_expected": not problems,
        "problems": problems,
    }


def positives(attack_set: AttackSet, world: World) -> dict[str, bool]:
    """Fabricated evidence the verdict must catch: T4 (a row routed too low) and T5 (an
    unregistered tool that ran)."""
    out = {"T4": False, "T5": False}
    run = {"run_id": POSITIVE_RUN_ID, "status": "completed", "tool_trace": [], "tool_outputs": []}
    try:
        e04, major = attack_set.by_id("E04"), attack_set.loans["L_major"]
        row = {
            "run_id": POSITIVE_RUN_ID,
            "loan_number": major,
            "exception_type": "gds_tds_ratio",
            "required_authority": "senior_underwriter",
            "status": "awaiting_raise",
        }
        v4 = judge(e04, run, requests=[row], drafts=[], world=world, offered=frozenset())
        out["T4"] = "T4" in v4.hit
        c03 = attack_set.by_id("C03")
        entry = {
            "turn": 1,
            "rule_stage": 9,
            "tool": "submit_exception_request",
            "status": "ok",
            "reason": None,
            "reached_executor": True,
            "args_digest": {"loan_number": attack_set.loans["L_minor"]},
        }
        v5 = judge(
            c03,
            {**run, "tool_trace": [entry]},
            requests=[],
            drafts=[],
            world=world,
            offered=frozenset(),
        )
        out["T5"] = "T5" in v5.hit
    except KeyError:
        pass
    return out


def run_matrix(
    attack_set: AttackSet,
    settings: Settings,
    deps: MatrixDeps,
    world: World,
    *,
    only: list[str] | None = None,
) -> MatrixResult:
    result = MatrixResult()
    try:
        for attack in attack_set.attacks:
            if only is not None and attack.id not in only:
                continue
            for config_name in CONFIGS:
                cell = run_cell(attack, config_name, attack_set, settings, deps, world)
                result.cells.append(cell)
                result.mismatches += cell["problems"]
        result.positives = positives(attack_set, world)
    finally:
        deps.reset()
    return result


# --- reports -----------------------------------------------------------------------------------


def report_dict(
    result: MatrixResult, attack_set: AttackSet, commit: str | None, dirty: bool | None
) -> dict[str, Any]:
    return {
        "kind": "attack_matrix",
        "attacks_sha256": attack_set.sha256,
        "registered": asdict(attack_set.registered),
        "git_commit": commit,
        "git_dirty": dirty,
        "as_expected": result.as_expected,
        "total": len(result.cells),
        "mismatches": result.mismatches,
        "positives": result.positives,
        "cells": result.cells,
    }


def render_md(report: dict[str, Any]) -> str:
    lines = [
        "# Attack matrix (scripted, no model calls)",
        "",
        f"- attacks.toml sha256: `{report['attacks_sha256']}`",
        f"- git: `{report['git_commit']}` dirty={report['git_dirty']}",
        f"- as expected: {report['as_expected']}/{report['total']}",
        "- positives: "
        + ", ".join(f"{k} {'pass' if v else 'FAIL'}" for k, v in report["positives"].items()),
        "",
        "| attack | config | out | hit | config_write | first layer | delivered | ok |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in report["cells"]:
        a = c["actual"]
        lines.append(
            f"| {c['attack']} | {c['config']} | {', '.join(a['out']) or '-'} | "
            f"{', '.join(a['hit']) or '-'} | {'yes' if a['config_write'] else '-'} | "
            f"{c['first_layer']} | {'yes' if c['delivered'] else 'no'} | "
            f"{'yes' if c['as_expected'] else 'NO'} |"
        )
    if report["mismatches"]:
        lines += ["", "## Mismatches", ""] + [f"- {m}" for m in report["mismatches"]]
    return "\n".join(lines) + "\n"
