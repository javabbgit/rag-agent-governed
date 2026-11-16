"""The policy layer: every tool call is judged here before anything runs.

Order, first match wins:
 1 tool unknown or not in the role      2 arguments not a JSON object      3 schema
 4 loan scope, recipient                5 argument provenance              6 call limits
 7 business rule (GDS/TDS exceptions)   8 side-effect tool -> draft only   9 execute
"""

from __future__ import annotations

import decimal
import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

import psycopg

from rag_agent.agent import tools
from rag_agent.agent.ctx import Ctx
from rag_agent.agent.errors import DbRejected, ToolArgumentError
from rag_agent.agent.mockdata import User, visible_to
from rag_agent.agent.schema import validate

# a loan number counts only as a whole token: not inside a longer word or a longer digit run
LOAN_RE = re.compile(r"(?<![A-Za-z0-9-])DMC-[0-9]{4}-[0-9]{5}(?![0-9])", re.IGNORECASE)
# executor failures that become error / executor_error; anything else propagates
EXECUTOR_ERRORS = (
    KeyError,
    TypeError,
    decimal.InvalidOperation,
    psycopg.errors.ForeignKeyViolation,
)
FREE_TEXT = ("subject", "body", "justification", "requested_value", "compensating_factors", "query")
PROVENANCE_TOOLS = ("draft_reply", "prepare_exception_request")
CLEAR_STRING_MAX = 64


@dataclass
class ToolCall:
    name: str
    arguments: Any  # raw JSON text from the model, or an already parsed object
    id: str | None = None

    @classmethod
    def coerce(cls, value: Any) -> ToolCall:
        if isinstance(value, ToolCall):
            return value
        if isinstance(value, dict):
            fn = value.get("function", value)
            return cls(str(fn.get("name", "")), fn.get("arguments"), value.get("id"))
        raise TypeError("not a tool call")


@dataclass
class ToolOutcome:
    status: str  # ok | no_basis | error | denied | awaiting_raise | pending_approval
    reason: str | None
    data: dict[str, Any]
    reached_executor: bool
    envelope: str
    step: int = 0


class _Stop(Exception):
    """Internal: a check ended the judgement."""

    def __init__(self, step: int, reason: str, field: str | None = None, reached: bool = False):
        self.step, self.reason, self.field, self.reached = step, reason, field, reached


# --- envelopes and logging -------------------------------------------------------------------


def _plain_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def make_envelope(tool: str, status: str, data: dict[str, Any], enveloped: bool) -> str:
    if enveloped:
        body = {"tool": tool, "status": status, "data": data}
        return json.dumps(body, sort_keys=True, ensure_ascii=False)
    if tool == "search_policy" and status == "ok":
        return "\n\n".join(c["text"] for c in data.get("chunks", []))
    lines = [f"status: {status}"]
    lines += [f"{k}: {_plain_value(v)}" for k, v in data.items()]
    return "\n".join(lines)


def _digest(text: str) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "len": len(text)}


def args_digest(raw: Any, args: dict[str, Any] | None, schema: dict[str, Any] | None) -> dict:
    """Free text and anything unexpected as {sha256, len}; known short fields in clear."""
    if args is None:
        return {"_raw": _digest(raw if isinstance(raw, str) else repr(type(raw)))}
    known = (schema or {}).get("properties", {})
    out: dict[str, Any] = {}
    for key, value in args.items():
        if key not in known or key in FREE_TEXT:
            out[key if key in known else "_unexpected"] = _digest(
                json.dumps(value, sort_keys=True, ensure_ascii=False)
            )
        elif isinstance(value, float) and not math.isfinite(value):
            out[key] = _digest(repr(value))  # NaN / Infinity never reach the jsonb column
        elif (
            isinstance(value, bool | int | float)
            or value is None
            or isinstance(value, str)
            and len(value) <= CLEAR_STRING_MAX
            or (
                isinstance(value, list)
                and len(value) <= 5
                and all(isinstance(v, str) and len(v) <= CLEAR_STRING_MAX for v in value)
            )
        ):
            out[key] = value
        else:
            out[key] = _digest(json.dumps(value, sort_keys=True, ensure_ascii=False))
    return out


# --- the judgement ---------------------------------------------------------------------------


def _parse_args(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _stop_scope(call: ToolCall, args: dict[str, Any], ctx: Ctx) -> dict[str, Any] | None:
    """Step 4. Returns the loan record for tools that name a loan."""
    if "loan_number" not in ctx.schemas[call.name].get("properties", {}):
        return None
    number = args.get("loan_number")
    if not isinstance(number, str):
        raise _Stop(3, "invalid_arguments", "loan_number")
    loan = ctx.repo.get_loan(number)
    # unknown and not permitted look the same
    if loan is None:
        raise _Stop(4, "not_found_or_not_permitted")
    pol = ctx.policy
    if pol.on("scope_check"):
        if not visible_to(loan, User(ctx.user_id, ctx.role), pol.roles[ctx.role].scope):
            raise _Stop(4, "not_found_or_not_permitted")
        if call.name == "draft_reply":
            contacts = {c["contact_id"] for c in ctx.repo.get_contacts(number)}
            if args.get("recipient_contact_id") not in contacts:
                raise _Stop(4, "recipient_not_permitted", "recipient_contact_id")
    return loan


def _stop_provenance(call: ToolCall, args: dict[str, Any], ctx: Ctx) -> None:
    """Step 5."""
    if call.name not in PROVENANCE_TOOLS:
        return
    mentioned = {m.upper() for m in LOAN_RE.findall(ctx.user_text)}
    if str(args.get("loan_number")).upper() not in mentioned:
        raise _Stop(5, "arg_provenance", "loan_number")
    if "supporting_chunk_ids" in args:
        ids = args["supporting_chunk_ids"]
        if not isinstance(ids, list) or any(i not in ctx.returned_chunk_ids for i in ids):
            raise _Stop(5, "arg_provenance", "supporting_chunk_ids")


def _judge(call: ToolCall, args: dict[str, Any] | None, ctx: Ctx) -> ToolOutcome:
    pol = ctx.policy
    role = pol.roles.get(ctx.role)
    # 1
    if call.name not in ctx.schemas or role is None:
        raise _Stop(1, "tool_not_permitted")
    if pol.on("role_allowlist") and call.name not in role.tools:
        raise _Stop(1, "tool_not_permitted")
    # 2
    if args is None:
        raise _Stop(2, "invalid_json")
    # 3
    if pol.on("schema_validation"):
        err = validate(ctx.schemas[call.name], args)
        if err:
            raise _Stop(3, "invalid_arguments", err[0] or None)
    # 4
    loan = _stop_scope(call, args, ctx)
    # 5
    if pol.on("arg_provenance"):
        _stop_provenance(call, args, ctx)
    # 6
    if ctx.message_calls > pol.limits.max_calls_per_message:
        raise _Stop(6, "too_many_calls")
    if (
        call.name == "prepare_exception_request"
        and ctx.exception_drafts >= pol.limits.max_exception_drafts_per_run
    ):
        raise _Stop(6, "duplicate_request")
    # 7 business rule: never switched off
    if call.name == "prepare_exception_request" and args.get("exception_type") == "gds_tds_ratio":
        assert loan is not None
        auth = tools.assess_loan(loan, ctx).authority
        if auth.required is None:
            raise _Stop(7, "no_failed_test")
        if auth.required == "not_exceptable":
            raise _Stop(7, "not_exceptable")
    # 8 / 9
    return _run(call, args, ctx)


def _run(call: ToolCall, args: dict[str, Any], ctx: Ctx) -> ToolOutcome:
    side_effect = call.name in ctx.policy.side_effect_tools
    guarded = side_effect and ctx.policy.on("approval_gate")
    step = 8 if guarded else 9
    reached = not guarded
    try:
        if guarded:
            data = tools.create_draft(args, ctx)
        elif side_effect:
            data = tools.execute_unguarded(args, ctx)
        else:
            data = tools.execute(call.name, args, ctx)
    except ToolArgumentError as exc:
        raise _Stop(9, "invalid_arguments", exc.field, reached=not guarded) from None
    except DbRejected:
        data = {"status": "error", "reason": "db_rejected"}
    except EXECUTOR_ERRORS:
        data = {"status": "error", "reason": "executor_error"}
    status = data["status"]
    body = {k: v for k, v in data.items() if k != "status"}
    outcome = ToolOutcome(status, data.get("reason"), body, reached, "", step)
    outcome.step = step
    return outcome


def _decision(outcome: ToolOutcome, guarded_draft: bool) -> str:
    if outcome.status == "denied":
        return "deny"
    if outcome.status == "error":
        return "error"
    return "draft" if guarded_draft else "allow"


def check_and_run(tool_call: Any, ctx: Ctx) -> ToolOutcome:
    """Judge one tool call and run it if allowed. Writes one policy_events row."""
    call = ToolCall.coerce(tool_call)
    ctx.message_calls += 1
    ctx.tool_calls += 1
    args = _parse_args(call.arguments)
    known = call.name in ctx.schemas
    try:
        outcome = _judge(call, args, ctx)
    except _Stop as stop:
        outcome = ToolOutcome(
            "denied",
            stop.reason,
            {"reason": stop.reason, "field": stop.field},
            stop.reached,
            "",
            stop.step,
        )
        ctx.denials += 1
    tool = call.name if known else "unknown"
    outcome.envelope = make_envelope(
        tool, outcome.status, outcome.data, ctx.policy.on("data_envelope")
    )
    guarded_draft = outcome.step == 8 and not outcome.reached_executor
    ctx.repo.log_event(
        {
            "run_id": ctx.run_id,
            "user_id": ctx.user_id,
            "role": ctx.role,
            "rule_stage": outcome.step,
            "step": ctx.step,
            "tool": tool,
            "decision": _decision(outcome, guarded_draft),
            "reason": outcome.reason,
            "reached_executor": outcome.reached_executor,
            "defenses": dict(ctx.policy.defenses),
            "args_digest": args_digest(call.arguments, args, ctx.schemas.get(call.name)),
        }
    )
    return outcome
