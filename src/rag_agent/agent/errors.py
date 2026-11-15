"""Errors of the agent layer. Messages are one line and never carry argument values or keys."""

from __future__ import annotations


class AgentError(RuntimeError):
    """Base class."""


class FactsMissingKey(AgentError):
    """A required key in corpus/facts.toml is missing or has the wrong type."""


class PolicyInvalid(AgentError):
    """config/agent_policy.toml is malformed."""


class DefensesOffUnacknowledged(AgentError):
    """A defense switch is off without AGENT_DEFENSES_OFF_ACK=1."""


class ToolsLockMismatch(AgentError):
    """The tool specs differ from tools.lock."""

    def __init__(
        self,
        changed: list[str] | None = None,
        missing: list[str] | None = None,
        extra: list[str] | None = None,
        detail: str = "",
    ):
        self.changed = sorted(changed or [])
        self.missing = sorted(missing or [])
        self.extra = sorted(extra or [])
        parts = []
        if self.changed:
            parts.append("changed: " + ", ".join(self.changed))
        if self.missing:
            parts.append("missing from specs: " + ", ".join(self.missing))
        if self.extra:
            parts.append("not in lock: " + ", ".join(self.extra))
        if detail:
            parts.append(detail)
        super().__init__("tool specs do not match tools.lock (" + "; ".join(parts) + ")")


class SpecInvalid(AgentError):
    """A tool spec is malformed or uses an unsupported schema keyword."""


class ToolArgumentError(AgentError):
    """Arguments that passed the schema but cannot be used (becomes invalid_arguments)."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


class DbRejected(AgentError):
    """The database refused a write through a CHECK constraint."""


class ApprovalError(AgentError):
    """Base class of the approvals errors; each is one line."""


class UnknownRequest(ApprovalError):
    pass


class NotAwaitingRaise(ApprovalError):
    pass


class NotPending(ApprovalError):
    pass


class NotFileUnderwriter(ApprovalError):
    pass


class NotApprover(ApprovalError):
    pass


class InsufficientAuthority(ApprovalError):
    pass


class SelfDecision(ApprovalError):
    pass


class ReasonRequired(ApprovalError):
    pass
