"""The live attack runner (D9-B): every live attack x repeat through the real agent loop.

No I/O of its own: the database, the loop and the clock come in through RunDeps, so tests run it
with fakes. Mirrors matrix.run_cell (reset, spec copy, lock preflight, config, run, rows, verdict)
and adds the abort and rerun rules of the live run.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from rag_agent.agent import specs
from rag_agent.agent_attack import report as rpt
from rag_agent.agent_attack.attacks import Attack, AttackSet
from rag_agent.agent_attack.harness import (
    config_for,
    offered_tools,
    preflight_lock,
    write_spec_copy,
)
from rag_agent.agent_attack.matrix import MatrixDeps
from rag_agent.agent_attack.verdict import StartupFailure, World, judge
from rag_agent.agent_eval.report import jsonable

# gateway error category -> seconds to wait before the one rerun; any other category aborts
RERUN_WAIT = {"rate_limited": 65.0, "timeout": 10.0, "gateway_unavailable": 10.0}
MODES = ("on", "off")
CONFIG_BY_MODE = {"on": "on", "off": "all_off"}


@dataclass
class RunDeps:
    matrix: MatrixDeps  # repo_for(attack), reset(), rows(run_id)
    run_fn: Callable[..., dict[str, Any]]  # (question, user, *, config, runtime, repo) -> asdict
    runtime_for: Callable[[Attack], Any]  # the mixed runtime for channel A, else the plain one
    sleep: Callable[[float], None]


@dataclass
class Outcome:
    runs: list[dict[str, Any]]
    aborted: str | None
    cost_usd: float


class Abort(Exception):
    pass


def execute_attack(
    attack: Attack,
    repeat: int,
    attempt: int,
    mode: str,
    ats: AttackSet,
    world: World,
    deps: RunDeps,
) -> dict[str, Any]:
    """reset -> config -> run -> read rows -> verdict. One run record; superseded is False."""
    deps.matrix.reset()
    config_name = CONFIG_BY_MODE[mode]
    run: Any
    offered: frozenset[str] | None = None
    with tempfile.TemporaryDirectory(prefix="attack_specs_") as tmp:
        if attack.spec_patch is not None:
            specs_path, lock_path = write_spec_copy(attack.spec_patch, tmp)
        else:
            specs_path, lock_path = specs.SPECS_PATH, specs.LOCK_PATH
        run = preflight_lock(lock_path, ats.registered)
        if run is None:
            cfg = config_for(config_name, specs_path=specs_path, lock_path=lock_path)
            if isinstance(cfg, StartupFailure):
                run = cfg
            else:
                run = deps.run_fn(
                    attack.question,
                    attack.user,
                    config=cfg,
                    runtime=deps.runtime_for(attack),
                    repo=deps.matrix.repo_for(attack),
                )
                offered = offered_tools(cfg, world.users[attack.user].role)
    res = jsonable(run) if isinstance(run, dict) else None
    requests: list[dict[str, Any]] = []
    drafts: list[dict[str, Any]] = []
    if res is not None:  # read right away, before any later reset
        requests, drafts = (jsonable(x) for x in deps.matrix.rows(res["run_id"]))
    verdict = judge(
        attack,
        res if res is not None else run,
        requests=requests,
        drafts=drafts,
        world=world,
        offered=offered or frozenset(),
    )
    base: dict[str, Any] = {
        "attack": attack.id,
        "channel": attack.channel,
        "repeat": repeat,
        "attempt": attempt,
        "superseded": False,
        "config": config_name,
    }
    if res is None:
        boot = isinstance(run, StartupFailure)
        base.update(
            run_id=None,
            status=rpt.NOT_RUN,
            reason=f"boot:{run.code}" if boot else "preflight",
            tool_trace=[],
            tool_outputs=[],
            offered_tools=None,
            call_ids=[],
            model_names=[],
            n_chat_calls=0,
            n_embed_calls=0,
            cost_usd=None,
            final_text="",
        )
    else:
        base.update(
            run_id=res["run_id"],
            status=res["status"],
            reason=res.get("reason"),
            tool_trace=res["tool_trace"],
            tool_outputs=res["tool_outputs"],
            offered_tools=sorted(offered or ()),
            call_ids=res["call_ids"],
            model_names=res["model_names"],
            n_chat_calls=res["n_chat_calls"],
            n_embed_calls=res["n_embed_calls"],
            cost_usd=res["cost_usd"],
            final_text=res["final_text"],
        )
    rec = {**base, **rpt.verdict_fields(verdict)}
    rec["pre_delivery"] = rpt.pre_delivery_of(rec)
    rec["rows"] = {"exception_requests": requests, "drafts": drafts}
    rec["r5_redactions"] = None
    return rec


def _row(rec: dict[str, Any]) -> str:
    tools = ",".join(str(e.get("tool")) for e in rec["tool_trace"]) or "-"
    cost = "n/a" if rec["cost_usd"] is None else f"{rec['cost_usd']:.6f}"
    return (
        f"{rec['attack']} r{rec['repeat']} a{rec['attempt']} | {rec['status']} |"
        f" {rec['reason'] or '-'} | {tools} | delivered={'yes' if rec['delivered'] else 'no'}"
        f" attempted={'yes' if rec['attempted'] else 'no'}"
        f" | hit {','.join(rec['targets_hit']) or '-'}"
        f" | layer {rec['first_layer']} | {rec['n_chat_calls']} chat | {cost}"
    )


def run_attacks(
    attacks: Sequence[Attack],
    ats: AttackSet,
    world: World,
    *,
    mode: str,
    repeats: int,
    cost_cap: float,
    deps: RunDeps,
    out: Callable[[str], None] = print,
) -> Outcome:
    """Every live attack x repeat in order. Rerun and abort rules as in the D9 manual."""
    runs: list[dict[str, Any]] = []
    cost = 0.0
    try:
        for attack in attacks:
            if attack.matrix_only:
                continue
            for repeat in range(1, repeats + 1):
                attempt = 1
                while True:
                    rec = execute_attack(attack, repeat, attempt, mode, ats, world, deps)
                    runs.append(rec)
                    cost += rec["cost_usd"] or 0.0
                    out(_row(rec))
                    if mode == "on" and rpt.unauthorized_total(rec) > 0:
                        out(f"[FAIL] unauthorized effect in {attack.id} r{repeat} {rec['run_id']}")
                        raise Abort(f"unauthorized effect in mode on ({attack.id} r{repeat})")
                    gateway = rec["status"] in rpt.GATEWAY_STATUSES
                    if not gateway and rec["n_chat_calls"] > 0 and rec["cost_usd"] is None:
                        raise Abort(f"cost header missing in {attack.id} r{repeat}")
                    if cost > cost_cap:
                        raise Abort(f"cost cap exceeded ({cost:.6f} > {cost_cap:.2f} USD)")
                    if not gateway:
                        break
                    if rec["status"] == "blocked":
                        if attack.expect_gateway:
                            break  # an expected gateway block is a result
                        raise Abort(f"unexpected gateway block in {attack.id} r{repeat}")
                    category = rec["reason"] or "unknown"
                    wait = RERUN_WAIT.get(category)
                    if wait is None:
                        raise Abort(f"gateway error {category} in {attack.id} r{repeat}")
                    if attempt > 1:
                        raise Abort(f"second gateway error ({category}) in {attack.id} r{repeat}")
                    rec["superseded"] = True
                    out(f"  {category}: waiting {wait:g} s, then rerunning {attack.id} r{repeat}")
                    deps.sleep(wait)
                    attempt += 1
    except Abort as exc:
        return Outcome(runs, str(exc), cost)
    except Exception as exc:  # database or setup failure: stop, keep what was collected
        return Outcome(runs, f"unexpected error ({type(exc).__name__})", cost)
    finally:
        try:
            deps.matrix.reset()
        except Exception:  # the reset must not hide the result of the run
            out("[FAIL] the final reset of the business tables failed")
    return Outcome(runs, None, cost)
