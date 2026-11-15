"""Approval state machine (P14, P25 non-SQL parts), the CLI wrapper, and the source-scan rule."""

import re
from pathlib import Path

import pytest

from agent_fakes import CHUNK_A, FakeRepo, call, exc_args, make_ctx
from exceptions_cli import run as cli_run
from rag_agent.agent import approvals
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
from rag_agent.agent.policy import check_and_run

MINOR, MAJOR = "DMC-2026-00102", "DMC-2026-00103"


def draft(repo, loan=MINOR, exception_type="gds_tds_ratio", user="uw-01"):
    ctx = make_ctx(user, loan, repo=repo)
    ctx.returned_chunk_ids.add(CHUNK_A)
    out = check_and_run(
        call("prepare_exception_request", **exc_args(loan, exception_type=exception_type)), ctx
    )
    assert out.status == "awaiting_raise", out
    return out.data["request_id"]


def test_p14_minor_file_walkthrough():
    repo = FakeRepo()
    rid = draft(repo)
    with pytest.raises(NotFileUnderwriter):
        approvals.raise_request(rid, "uw-02", repo=repo)
    assert approvals.raise_request(rid, "uw-01", repo=repo)["status"] == "pending_approval"
    with pytest.raises(NotApprover):
        approvals.approve(rid, "uw-01", repo=repo)
    assert approvals.approve(rid, "su-01", repo=repo)["status"] == "approved"
    with pytest.raises(NotPending):
        approvals.approve(rid, "su-02", repo=repo)


def test_p14_major_file_needs_committee():
    repo = FakeRepo()
    rid = draft(repo, MAJOR)
    approvals.raise_request(rid, "uw-01", repo=repo)
    with pytest.raises(InsufficientAuthority):
        approvals.approve(rid, "su-01", repo=repo)
    assert approvals.approve(rid, "cc-01", repo=repo)["status"] == "approved"


def test_p25_ltv_and_income_documents():
    repo = FakeRepo()
    ltv = draft(repo, exception_type="ltv")
    docs = draft(repo, exception_type="income_documents")
    for rid in (ltv, docs):
        approvals.raise_request(rid, "uw-01", repo=repo)
    with pytest.raises(InsufficientAuthority):
        approvals.approve(ltv, "su-01", repo=repo)
    assert approvals.approve(ltv, "cc-01", repo=repo)["status"] == "approved"
    assert approvals.approve(docs, "su-01", repo=repo)["status"] == "approved"


def test_nobody_decides_their_own_request():
    repo = FakeRepo()
    rid = draft(repo)
    approvals.raise_request(rid, "uw-01", repo=repo)
    # an approver who prepared the request cannot decide it
    repo.requests[0]["prepared_by"] = "su-01"
    with pytest.raises(SelfDecision):
        approvals.approve(rid, "su-01", repo=repo)
    repo.requests[0]["prepared_by"] = "uw-01"
    repo.requests[0]["raised_by"] = "su-01"
    with pytest.raises(SelfDecision):
        approvals.refuse(rid, "su-01", "no", repo=repo)


def test_refuse_and_discard_need_a_reason_and_right_state():
    repo = FakeRepo()
    rid = draft(repo)
    with pytest.raises(ReasonRequired):
        approvals.discard(rid, "uw-01", " ", repo=repo)
    with pytest.raises(NotPending):
        approvals.refuse(rid, "su-01", "no", repo=repo)
    assert approvals.discard(rid, "uw-01", "not needed", repo=repo)["status"] == "withdrawn"
    with pytest.raises(NotAwaitingRaise):
        approvals.raise_request(rid, "uw-01", repo=repo)
    rid2 = draft(repo)
    approvals.raise_request(rid2, "uw-01", repo=repo)
    with pytest.raises(NotAwaitingRaise):
        approvals.discard(rid2, "uw-01", "late", repo=repo)
    with pytest.raises(ReasonRequired):
        approvals.refuse(rid2, "su-01", "", repo=repo)
    done = approvals.refuse(rid2, "su-01", "weak case", repo=repo)
    assert done["status"] == "refused" and done["decided_reason"] == "weak case"


def test_unknown_request_and_unknown_user():
    repo = FakeRepo()
    with pytest.raises(UnknownRequest):
        approvals.raise_request("ER-999999", "uw-01", repo=repo)
    rid = draft(repo)
    with pytest.raises(NotFileUnderwriter):
        approvals.raise_request(rid, "nobody", repo=repo)


def test_list_is_oldest_first_and_filters():
    repo = FakeRepo()
    a, b = draft(repo), draft(repo, MAJOR)
    approvals.raise_request(a, "uw-01", repo=repo)
    assert [r["request_id"] for r in approvals.list_requests(repo=repo)] == [a, b]
    assert [r["request_id"] for r in approvals.list_requests("awaiting_raise", repo=repo)] == [b]


def test_cli_confirmation_and_error_lines(capsys):
    repo = FakeRepo()
    rid = draft(repo)
    lines: list[str] = []
    assert cli_run(["raise", rid, "--as", "uw-01"], repo, ask=lambda _: "n", out=lines.append) == 1
    shown = "\n".join(lines)
    assert "gds_tds_ratio" in shown and "senior_underwriter" in shown
    assert repo.requests[0]["status"] == "awaiting_raise"
    capsys.readouterr()
    assert cli_run(["raise", rid, "--as", "uw-02", "--yes"], repo, out=lines.append) == 1
    assert capsys.readouterr().err.startswith("NotFileUnderwriter")
    lines.clear()
    assert cli_run(["raise", rid, "--as", "uw-01", "--yes"], repo, out=lines.append) == 0
    assert repo.requests[0]["status"] == "pending_approval"
    lines.clear()
    assert cli_run(["list", "--ids-only"], repo, out=lines.append) == 0 and lines == [rid]


def test_only_approvals_module_changes_status_after_awaiting_raise():
    root = Path(__file__).resolve().parents[1]
    allowed = {"approvals.py", "db.py"}
    for path in [*(root / "src").rglob("*.py"), *(root / "scripts").glob("*.py")]:
        if path.name in allowed:
            continue
        assert not re.search(r"\.transition\(", path.read_text(encoding="utf-8")), path.name
