"""Per-run state shared by the tools and the policy layer."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rag_agent import answer
from rag_agent.agent.db import Repo
from rag_agent.agent.facts import Facts
from rag_agent.agent.policy_config import AgentPolicy


@dataclass
class Ctx:
    """Identity always comes from here, never from tool arguments."""

    user_id: str
    role: str
    user_text: str
    repo: Repo
    policy: AgentPolicy
    facts: Facts
    schemas: dict[str, dict[str, Any]]  # tool name -> parameters schema
    runtime: Any = None  # answer.Runtime, needed only by search_policy
    prepare_fn: Callable[[str, Any], Any] = answer.prepare
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    returned_chunk_ids: set[str] = field(default_factory=set)
    markers: dict[str, str] = field(default_factory=dict)  # chunk_id -> S1..
    draft_ids: list[str] = field(default_factory=list)
    exception_drafts: int = 0
    message_calls: int = 0
    tool_calls: int = 0
    denials: int = 0
    step: int | None = None  # agent-loop turn number; D7-B sets it

    def begin_message(self) -> None:
        """Call once per assistant message, before judging its tool calls."""
        self.message_calls = 0

    def marker_for(self, chunk_id: str) -> str:
        """S1.. continuing across calls in one run; a repeated chunk keeps its marker."""
        if chunk_id not in self.markers:
            self.markers[chunk_id] = f"S{len(self.markers) + 1}"
        return self.markers[chunk_id]

    @property
    def denial_limit_reached(self) -> bool:
        return self.denials >= self.policy.limits.max_denials

    @property
    def tool_limit_reached(self) -> bool:
        return self.tool_calls >= self.policy.limits.max_tool_calls
