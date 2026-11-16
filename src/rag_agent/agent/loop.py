"""The agent loop: chat with tools through the one LLMClient, every tool call through the policy
layer. Fail closed: zero retries, no other model, no other endpoint; this module never builds a
client and never touches the approval state machine.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from rag_agent import answer, citations
from rag_agent import messages as d6
from rag_agent.agent import boot, policy, runlog, specs
from rag_agent.agent import messages as msg
from rag_agent.agent.ctx import Ctx
from rag_agent.agent.db import PgRepo, Repo
from rag_agent.agent.errors import (
    AgentStartupError,
    DefensesOffUnacknowledged,
    FactsMissingKey,
    PolicyInvalid,
    SpecInvalid,
    ToolsLockMismatch,
)
from rag_agent.agent.mockdata import User, load_users
from rag_agent.config import ConfigError, Settings, load_settings
from rag_agent.errors import LLMError

STARTUP_CODES: list[tuple[type[Exception], str]] = [
    (ToolsLockMismatch, "tools_lock_mismatch"),
    (PolicyInvalid, "policy_invalid"),
    (FactsMissingKey, "facts_missing_key"),
    (DefensesOffUnacknowledged, "defenses_off_unacknowledged"),
    (SpecInvalid, "specs_invalid"),
    (ConfigError, "config_invalid"),
]
REJECT_MESSAGES = {"unknown_user": msg.C4, "empty": d6.B11a, "too_long": d6.B11b}
DRAFT_TOOL = "prepare_exception_request"
_STARTED: tuple[boot.AgentConfig, answer.Runtime] | None = None


@dataclass
class AgentResult:
    run_id: str
    status: str = ""  # completed | awaiting_raise | policy_stopped | step_limit | blocked | error
    reason: str | None = None  # ... | rejected_input
    final_text: str = ""
    message: str = ""
    exception_request_ids: list[str] = field(default_factory=list)
    draft_ids: list[str] = field(default_factory=list)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    sources: list[list[str]] = field(default_factory=list)  # [marker, chunk_id]
    cited_markers: list[str] = field(default_factory=list)
    unknown_markers: list[str] = field(default_factory=list)
    uncited_units: list[str] = field(default_factory=list)
    n_chat_calls: int = 0
    n_tool_calls: int = 0
    n_denials: int = 0
    n_embed_calls: int = 0
    call_ids: list[str] = field(default_factory=list)
    model_names: list[str] = field(default_factory=list)
    cost_usd: float | None = None
    latency_ms_total: float = 0.0
    chat_latency_ms: list[float] = field(default_factory=list)
    finish_reason: str | None = None  # of the last chat response
    prompt_sha256: str | None = None
    tools_lock_sha256: str | None = None
    policy_sha256: str | None = None
    defenses: dict[str, bool] = field(default_factory=dict)


def startup(
    settings: Settings | None = None, **runtime_kw: Any
) -> tuple[boot.AgentConfig, answer.Runtime]:
    """Once per process, before any gateway request: config, agent prompt, then the runtime."""
    global _STARTED
    try:
        config = boot.load_agent_config()
        settings = settings or load_settings()
        answer.load_prompt(settings.agent_prompt)
        runtime = answer.build_runtime(settings, **runtime_kw)
    except tuple(cls for cls, _ in STARTUP_CODES) as exc:
        code = next(code for cls, code in STARTUP_CODES if isinstance(exc, cls))
        raise AgentStartupError(code, str(exc)) from None
    _STARTED = (config, runtime)
    return _STARTED


def _parsed(raw: Any) -> dict[str, Any] | None:
    """The arguments as the policy layer reads them: a JSON object, or None."""
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _finish(res: AgentResult, status: str, reason: str | None) -> None:
    """Status, citation bookkeeping (never changes the status) and the user-facing message."""
    res.status, res.reason = status, reason
    units = citations.split_units(res.final_text)
    known = {marker for marker, _ in res.sources}
    cited = list(dict.fromkeys(f"S{n}" for u in units for n in citations.parse_markers(u)))
    res.cited_markers = [m for m in cited if m in known]
    res.unknown_markers = [m for m in cited if m not in known]
    res.uncited_units = [u for u in units if not citations.parse_markers(u)]
    if status == "completed":
        res.message = res.final_text
    elif status == "awaiting_raise":
        res.message = f"{res.final_text}\n\n{msg.C1}"
    elif status == "rejected_input":
        res.message = REJECT_MESSAGES[reason or ""]
    elif status == "error":
        res.message = d6.ERROR_MESSAGES[reason or ""]
    else:
        res.message = {"policy_stopped": msg.C2, "step_limit": msg.C3, "blocked": d6.B4}[status]


def _loop(
    res: AgentResult,
    text: str,
    user: User,
    config: boot.AgentConfig,
    runtime: answer.Runtime,
    repo: Repo,
    prompt_text: str,
) -> tuple[str, str | None]:
    pol, limits = config.policy, config.policy.limits
    ctx = Ctx(
        user.user_id, user.role, text, repo, pol, config.facts, config.schemas, runtime=runtime
    )
    ctx.run_id = res.run_id
    base_prepare = ctx.prepare_fn

    def counting_prepare(query: str, rt: Any) -> Any:
        prep = base_prepare(query, rt)
        res.n_embed_calls += 1 if prep.embedded else 0
        return prep

    ctx.prepare_fn = counting_prepare
    tools = config.tools_for_role(user.role) if pol.on("role_allowlist") else config.specs
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": prompt_text},
        {"role": "user", "content": text},
    ]
    awaiting = False
    try:
        for turn in range(1, limits.max_chat_calls + 1):
            ctx.step = turn
            res.n_chat_calls += 1  # a failed request counts too
            try:
                chat = runtime.client.chat_tools(
                    messages,
                    tools,
                    tool_choice="auto",
                    max_tokens=runtime.settings.agent_max_tokens,
                )
            except LLMError as exc:  # no retry, no other model, no other endpoint
                if exc.call_id:
                    res.call_ids.append(exc.call_id)
                category = answer.categorize(exc)
                return ("blocked" if category == "blocked_policy" else "error"), category
            res.finish_reason = chat.finish_reason
            res.chat_latency_ms.append(round(chat.latency_ms, 1))
            if chat.call_id:
                res.call_ids.append(chat.call_id)
            name = chat.resolved_model or chat.model
            if name and name not in res.model_names:
                res.model_names.append(name)
            if chat.cost_usd is not None:
                res.cost_usd = (res.cost_usd or 0.0) + chat.cost_usd
            messages.append(chat.message)  # echoed exactly as returned
            calls = chat.message.get("tool_calls") or []
            if not calls:
                content = chat.message.get("content")
                res.final_text = content.strip() if isinstance(content, str) else ""
                return ("awaiting_raise" if awaiting else "completed"), None
            ctx.begin_message()
            for call in calls:
                t0 = time.perf_counter()
                outcome = policy.check_and_run(call, ctx)  # counts and logs the call itself
                messages.append(
                    {"role": "tool", "tool_call_id": call.get("id"), "content": outcome.envelope}
                )
                fn = call.get("function") or {}
                known = fn.get("name") in config.schemas
                raw = fn.get("arguments")
                res.tool_trace.append(
                    {
                        "turn": turn,
                        "rule_stage": outcome.step,
                        "tool": fn.get("name") if known else "unknown",
                        "status": outcome.status,
                        "reason": outcome.reason,
                        "reached_executor": outcome.reached_executor,
                        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                        "args_digest": policy.args_digest(
                            raw, _parsed(raw), config.schemas.get(fn.get("name"))
                        ),
                    }
                )
                drafted = outcome.status in ("awaiting_raise", "pending_approval")
                if fn.get("name") == DRAFT_TOOL and drafted:
                    if outcome.data["request_id"] not in res.exception_request_ids:
                        res.exception_request_ids.append(outcome.data["request_id"])
                    awaiting = awaiting or outcome.status == "awaiting_raise"
            if ctx.tool_calls > limits.max_tool_calls:
                return "step_limit", "max_tool_calls"
            if ctx.denials >= limits.max_denials:
                return "policy_stopped", "too_many_denials"
        return "step_limit", "max_chat_calls"
    finally:
        res.n_tool_calls, res.n_denials = ctx.tool_calls, ctx.denials
        res.draft_ids = list(ctx.draft_ids)
        res.sources = [[marker, chunk_id] for chunk_id, marker in ctx.markers.items()]


def run_agent(
    question: str,
    user_id: str,
    *,
    config: boot.AgentConfig | None = None,
    runtime: answer.Runtime | None = None,
    repo: Repo | None = None,
    log: bool = True,
    log_dir: str | None = None,
    source: str = "interactive",
) -> AgentResult:
    """One agent run for one user question. Exceptions other than LLMError propagate."""
    if config is None or runtime is None:
        started = _STARTED or startup()
        config, runtime = config or started[0], runtime or started[1]
    settings = runtime.settings
    prompt_text, prompt_sha = answer.load_prompt(settings.agent_prompt)
    t_start = time.perf_counter()
    res = AgentResult(
        run_id=uuid.uuid4().hex[:12],
        prompt_sha256=prompt_sha,
        tools_lock_sha256=specs.total_hash(specs.hashes(config.specs)),
        policy_sha256=config.policy.sha256,
        defenses=dict(config.policy.defenses),
    )
    user = load_users().get(user_id)
    text = question.strip()
    if user is None:
        status, reason = "rejected_input", "unknown_user"
    elif not text:
        status, reason = "rejected_input", "empty"
    elif len(text) > settings.question_max_chars:
        status, reason = "rejected_input", "too_long"
    else:
        status, reason = _loop(res, text, user, config, runtime, repo or PgRepo(), prompt_text)
    _finish(res, status, reason)
    res.latency_ms_total = round((time.perf_counter() - t_start) * 1000, 1)
    if log:
        line = runlog.log_line(res, settings, user_id, user.role if user else None, text, source)
        runlog.write_log(line, log_dir or settings.agent_log_dir)
    return res
