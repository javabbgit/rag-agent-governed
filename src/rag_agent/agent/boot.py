"""Startup: facts, policy and pinned tool specs, loaded once and checked before any model call."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rag_agent.agent.facts import Facts, load_facts
from rag_agent.agent.policy_config import AgentPolicy, load_policy
from rag_agent.agent.specs import load_pinned


@dataclass(frozen=True)
class AgentConfig:
    facts: Facts
    policy: AgentPolicy
    specs: list[dict[str, Any]]

    @property
    def schemas(self) -> dict[str, dict[str, Any]]:
        return {s["function"]["name"]: s["function"]["parameters"] for s in self.specs}

    def tools_for_role(self, role: str) -> list[dict[str, Any]]:
        """The tool objects sent to the model for this role (the allow-list, pinned specs)."""
        allowed = self.policy.roles[role].tools
        return [s for s in self.specs if s["function"]["name"] in allowed]


def load_agent_config(
    facts_path: str | Path | None = None,
    policy_path: str | Path | None = None,
    specs_path: str | Path | None = None,
    lock_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> AgentConfig:
    """Raises FactsMissingKey, PolicyInvalid, DefensesOffUnacknowledged or ToolsLockMismatch."""
    env = os.environ if env is None else env
    facts = load_facts(facts_path)
    policy = load_policy(policy_path, env)
    specs = load_pinned(policy.on("tool_pinning"), specs_path, lock_path)
    facts.key_value(policy.senior_max_key)
    facts.key_value(policy.committee_max_key)
    for spec in specs:
        codes = spec["function"]["parameters"].get("properties", {}).get("product_code", {})
        for code in codes.get("enum", []):
            facts.rate_for(code)  # every product code must resolve in the rate sheet
    return AgentConfig(facts, policy, specs)
