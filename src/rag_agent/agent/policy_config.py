"""config/agent_policy.toml: loading, validation, defense switches."""

from __future__ import annotations

import hashlib
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rag_agent.agent.errors import DefensesOffUnacknowledged, PolicyInvalid
from rag_agent.agent.facts import REQUIRED_KEYS
from rag_agent.agent.specs import TOOL_NAMES

POLICY_PATH = Path(__file__).resolve().parents[3] / "config" / "agent_policy.toml"

ROLES = ("broker", "underwriter", "senior_underwriter", "credit_committee")
SCOPES = ("own_submitted", "assigned", "all")
DEFENSES = (
    "role_allowlist",
    "schema_validation",
    "tool_pinning",
    "scope_check",
    "arg_provenance",
    "approval_gate",
    "data_envelope",
)
LIMIT_KEYS = (
    "max_chat_calls",
    "max_tool_calls",
    "max_calls_per_message",
    "max_denials",
    "max_exception_drafts_per_run",
)
EXCEPTION_TYPES = ("gds_tds_ratio", "ltv", "income_documents", "rate_hold")
APPROVER_ROLES = ("senior_underwriter", "credit_committee")  # in tier order


@dataclass(frozen=True)
class Limits:
    max_chat_calls: int
    max_tool_calls: int
    max_calls_per_message: int
    max_denials: int
    max_exception_drafts_per_run: int


@dataclass(frozen=True)
class RolePolicy:
    tools: tuple[str, ...]
    scope: str


@dataclass(frozen=True)
class AgentPolicy:
    limits: Limits
    roles: dict[str, RolePolicy]
    side_effect_tools: tuple[str, ...]
    senior_max_key: str  # facts key names, not numbers
    committee_max_key: str
    minimum_role: dict[str, str]
    defenses: dict[str, bool]
    sha256: str
    off: tuple[str, ...] = field(default=())

    def on(self, name: str) -> bool:
        return self.defenses[name]


def _fail(msg: str) -> PolicyInvalid:
    return PolicyInvalid(msg)


def _only_keys(table: Any, allowed: tuple[str, ...], where: str, exact: bool = True) -> None:
    if not isinstance(table, dict):
        raise _fail(f"{where} must be a table")
    unknown = set(table) - set(allowed)
    if unknown:
        raise _fail(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")
    missing = set(allowed) - set(table)
    if exact and missing:
        raise _fail(f"{where}: missing key(s): {', '.join(sorted(missing))}")


def _parse(data: dict[str, Any], digest: str, disabled: tuple[str, ...]) -> AgentPolicy:
    _only_keys(data, ("limits", "roles", "side_effect", "authority", "defenses"), "policy")

    _only_keys(data["limits"], LIMIT_KEYS, "limits")
    for key, value in data["limits"].items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise _fail(f"limits.{key} must be a positive integer")
    limits = Limits(**data["limits"])

    _only_keys(data["roles"], ROLES, "roles")
    roles: dict[str, RolePolicy] = {}
    for name, role in data["roles"].items():
        _only_keys(role, ("tools", "scope"), f"roles.{name}")
        tools = role["tools"]
        if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
            raise _fail(f"roles.{name}.tools must be a list of names")
        if set(tools) - set(TOOL_NAMES):
            raise _fail(f"roles.{name}.tools names an unknown tool")
        if role["scope"] not in SCOPES:
            raise _fail(f"roles.{name}.scope is not a known scope")
        roles[name] = RolePolicy(tuple(tools), role["scope"])

    _only_keys(data["side_effect"], ("tools",), "side_effect")
    side = data["side_effect"]["tools"]
    if not isinstance(side, list) or set(side) - set(TOOL_NAMES):
        raise _fail("side_effect.tools names an unknown tool")

    auth = data["authority"]
    _only_keys(auth, ("gds_tds_ratio", "minimum_role"), "authority")
    ratio = auth["gds_tds_ratio"]
    _only_keys(ratio, ("senior_underwriter_max_pts", "credit_committee_max_pts"), "authority")
    for key in ratio.values():
        if key not in REQUIRED_KEYS:
            raise _fail("authority names a facts key that is not a required key")
    minimum = auth["minimum_role"]
    _only_keys(minimum, tuple(t for t in EXCEPTION_TYPES if t != "gds_tds_ratio"), "authority")
    if any(v not in APPROVER_ROLES for v in minimum.values()):
        raise _fail("authority.minimum_role has an unknown role")

    _only_keys(data["defenses"], DEFENSES, "defenses")
    if not all(isinstance(v, bool) for v in data["defenses"].values()):
        raise _fail("defenses values must be booleans")
    defenses = {k: v and k not in disabled for k, v in data["defenses"].items()}

    return AgentPolicy(
        limits=limits,
        roles=roles,
        side_effect_tools=tuple(side),
        senior_max_key=ratio["senior_underwriter_max_pts"],
        committee_max_key=ratio["credit_committee_max_pts"],
        minimum_role=dict(minimum),
        defenses=defenses,
        sha256=digest,
        off=disabled,
    )


def parse_disable(env: Mapping[str, str]) -> tuple[str, ...]:
    raw = env.get("AGENT_DISABLE", "")
    names = tuple(n.strip() for n in raw.split(",") if n.strip())
    unknown = set(names) - set(DEFENSES)
    if unknown:
        raise _fail("AGENT_DISABLE names an unknown defense")
    return names


def load_policy(
    path: str | Path | None = None, env: Mapping[str, str] | None = None
) -> AgentPolicy:
    """Load and validate the policy file; apply AGENT_DISABLE (needs AGENT_DEFENSES_OFF_ACK=1)."""
    env = os.environ if env is None else env
    p = Path(path or POLICY_PATH)
    try:
        raw = p.read_bytes()
        data = tomllib.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        raise _fail(f"policy file is missing or unreadable: {p.name}") from None
    policy = _parse(data, hashlib.sha256(raw).hexdigest(), parse_disable(env))
    if not all(policy.defenses.values()) and env.get("AGENT_DEFENSES_OFF_ACK") != "1":
        raise DefensesOffUnacknowledged(
            "a defense switch is off; set AGENT_DEFENSES_OFF_ACK=1 to acknowledge (tests only)"
        )
    return policy
