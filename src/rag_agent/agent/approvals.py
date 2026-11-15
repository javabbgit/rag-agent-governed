"""EXC-100 state machine after the draft. The only code that changes a status after
awaiting_raise: raise, discard, approve, refuse. Humans only; the agent has no path here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from rag_agent.agent.db import PgRepo, Repo
from rag_agent.agent.errors import (
    InsufficientAuthority,
    NotApprover,
    NotAwaitingRaise,
    NotFileUnderwriter,
    NotPending,
    ReasonRequired,
    SelfDecision,
    UnknownRequest,
)
from rag_agent.agent.mockdata import User, load_users
from rag_agent.agent.policy_config import APPROVER_ROLES

STATUSES = ("awaiting_raise", "pending_approval", "approved", "refused", "withdrawn")


def _now() -> datetime:
    return datetime.now(UTC)


def _repo(repo: Repo | None) -> Repo:
    return repo or PgRepo()


def _user(user_id: str, users: dict[str, User] | None) -> User | None:
    return (users if users is not None else load_users()).get(user_id)


def _request(repo: Repo, request_id: str) -> dict[str, Any]:
    row = repo.get_request(request_id)
    if row is None:
        raise UnknownRequest("unknown request")
    return row


def _file_underwriter(repo: Repo, request: dict[str, Any], user_id: str) -> None:
    loan = repo.get_loan(request["loan_number"])
    if loan is None or loan["underwriter_id"] != user_id:
        raise NotFileUnderwriter("only the underwriter on this file may do that")


def _need_reason(reason: str | None) -> str:
    if not reason or not reason.strip():
        raise ReasonRequired("a reason is required")
    return reason.strip()


def list_requests(status: str | None = None, *, repo: Repo | None = None) -> list[dict[str, Any]]:
    """Oldest first."""
    return _repo(repo).list_requests(status)


def get_request(request_id: str, *, repo: Repo | None = None) -> dict[str, Any]:
    return _request(_repo(repo), request_id)


def raise_request(request_id: str, user_id: str, *, repo: Repo | None = None) -> dict[str, Any]:
    repo = _repo(repo)
    req = _request(repo, request_id)
    if req["status"] != "awaiting_raise":
        raise NotAwaitingRaise("the request is not awaiting a raise")
    _file_underwriter(repo, req, user_id)
    fields = {"raised_by": user_id, "raised_at": _now()}
    if not repo.transition(request_id, "awaiting_raise", "pending_approval", fields):
        raise NotAwaitingRaise("the request is not awaiting a raise")
    return _request(repo, request_id)


def discard(
    request_id: str, user_id: str, reason: str, *, repo: Repo | None = None
) -> dict[str, Any]:
    repo = _repo(repo)
    req = _request(repo, request_id)
    if req["status"] != "awaiting_raise":
        raise NotAwaitingRaise("the request is not awaiting a raise")
    _file_underwriter(repo, req, user_id)
    fields = {"decided_by": user_id, "decided_at": _now(), "decided_reason": _need_reason(reason)}
    if not repo.transition(request_id, "awaiting_raise", "withdrawn", fields):
        raise NotAwaitingRaise("the request is not awaiting a raise")
    return _request(repo, request_id)


def _decide(
    request_id: str,
    user_id: str,
    to_status: str,
    reason: str | None,
    repo: Repo | None,
    users: dict[str, User] | None,
) -> dict[str, Any]:
    repo = _repo(repo)
    req = _request(repo, request_id)
    if req["status"] != "pending_approval":
        raise NotPending("the request is not pending approval")
    user = _user(user_id, users)
    if user is None or user.role not in APPROVER_ROLES:
        raise NotApprover("only a senior underwriter or the credit committee may decide")
    required = req["required_authority"]
    if required not in APPROVER_ROLES or APPROVER_ROLES.index(user.role) < APPROVER_ROLES.index(
        required
    ):
        raise InsufficientAuthority("the required authority is higher than your role")
    if user_id in (req["prepared_by"], req["raised_by"]):
        raise SelfDecision("you cannot decide a request you prepared or raised")
    fields: dict[str, Any] = {"decided_by": user_id, "decided_at": _now()}
    if reason is not None:
        fields["decided_reason"] = _need_reason(reason)
    if not repo.transition(request_id, "pending_approval", to_status, fields):
        raise NotPending("the request is not pending approval")
    return _request(repo, request_id)


def approve(
    request_id: str,
    user_id: str,
    *,
    repo: Repo | None = None,
    users: dict[str, User] | None = None,
) -> dict[str, Any]:
    return _decide(request_id, user_id, "approved", None, repo, users)


def refuse(
    request_id: str,
    user_id: str,
    reason: str,
    *,
    repo: Repo | None = None,
    users: dict[str, User] | None = None,
) -> dict[str, Any]:
    return _decide(request_id, user_id, "refused", _need_reason(reason), repo, users)
