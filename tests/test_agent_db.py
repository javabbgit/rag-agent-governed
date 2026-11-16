"""DB tests for the agent schema: need local Postgres. Run with `make test-db`."""

import os
import re

import psycopg
import pytest

from agent_fakes import CHUNK_A, call, exc_args, make_ctx
from rag_agent.agent import approvals, mockdata
from rag_agent.agent.db import PgRepo, seed
from rag_agent.agent.errors import (
    InsufficientAuthority,
    NotApprover,
    NotFileUnderwriter,
    NotPending,
)
from rag_agent.agent.policy import check_and_run

pytestmark = pytest.mark.db

DSN = os.environ.get("DATABASE_URL")
MINOR, MAJOR = "DMC-2026-00102", "DMC-2026-00103"
ER_ID = re.compile(r"^ER-[0-9]{6}$")
COUNTS = "SELECT (SELECT count(*) FROM agent.loans), (SELECT count(*) FROM agent.contacts)"


@pytest.fixture(autouse=True)
def fresh():
    if not DSN:
        pytest.skip("DATABASE_URL is not set")
    seed(DSN, mockdata.load_loans(), mockdata.load_contacts(), reset=True)
    yield
    seed(DSN, mockdata.load_loans(), mockdata.load_contacts(), reset=True)


@pytest.fixture
def repo():
    return PgRepo(DSN)


def draft(repo, loan=MINOR, exception_type="gds_tds_ratio", env=None):
    ctx = make_ctx("uw-01", loan, repo=repo, env=env)
    ctx.returned_chunk_ids.add(CHUNK_A)
    return check_and_run(
        call("prepare_exception_request", **exc_args(loan, exception_type=exception_type)), ctx
    )


def sql(query, params=()):
    with psycopg.connect(DSN) as conn:
        return conn.execute(query, params).fetchall()


def test_seed_is_idempotent():
    before = sql(COUNTS)
    seed(DSN, mockdata.load_loans(), mockdata.load_contacts())
    assert sql(COUNTS) == before
    assert before == [(12, len(mockdata.load_contacts()))]


def test_p3_draft_is_awaiting_raise(repo):
    out = draft(repo)
    assert out.status == "awaiting_raise" and ER_ID.match(out.data["request_id"])
    assert sql(
        "SELECT count(*) FROM agent.exception_requests WHERE status <> 'awaiting_raise'"
    ) == [(0,)]


def test_open_request_unique_index(repo):
    first = draft(repo)
    second = draft(repo)
    assert second.data["already_open"] is True
    assert second.data["request_id"] == first.data["request_id"]
    assert sql("SELECT count(*) FROM agent.exception_requests") == [(1,)]


def test_p14_walkthrough_and_check_constraints(repo):
    rid = draft(repo).data["request_id"]
    with pytest.raises(NotFileUnderwriter):
        approvals.raise_request(rid, "uw-02", repo=repo)
    approvals.raise_request(rid, "uw-01", repo=repo)
    with pytest.raises(NotApprover):
        approvals.approve(rid, "uw-01", repo=repo)
    assert approvals.approve(rid, "su-01", repo=repo)["status"] == "approved"
    with pytest.raises(NotPending):
        approvals.approve(rid, "su-02", repo=repo)

    major = draft(repo, MAJOR).data["request_id"]
    approvals.raise_request(major, "uw-01", repo=repo)
    with pytest.raises(InsufficientAuthority):
        approvals.approve(major, "su-01", repo=repo)
    assert approvals.approve(major, "cc-01", repo=repo)["status"] == "approved"


def test_raw_update_to_approved_is_rejected_by_checks(repo):
    rid = draft(repo).data["request_id"]
    approvals.raise_request(rid, "uw-01", repo=repo)
    for decided_by in (None, "uw-01"):
        with psycopg.connect(DSN) as conn, pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE agent.exception_requests SET status = 'approved', decided_by = %s"
                " WHERE request_id = %s",
                (decided_by, rid),
            )
    with psycopg.connect(DSN) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "UPDATE agent.exception_requests SET status = 'pending_approval', raised_by = NULL"
            " WHERE request_id = %s",
            (rid,),
        )
    with psycopg.connect(DSN) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "UPDATE agent.exception_requests SET status = 'withdrawn' WHERE request_id = %s", (rid,)
        )


def test_p17_gate_off_insert_as_approved_is_rejected(repo):
    env = {"AGENT_DISABLE": "approval_gate", "AGENT_DEFENSES_OFF_ACK": "1"}
    out = draft(repo, env=env)
    assert (out.status, out.reason) == ("error", "db_rejected") and out.reached_executor
    assert sql("SELECT count(*) FROM agent.exception_requests WHERE status = 'approved'") == [(0,)]


def test_p25_ltv_and_income_documents(repo):
    ltv = draft(repo, exception_type="ltv").data["request_id"]
    docs = draft(repo, exception_type="income_documents").data["request_id"]
    for rid in (ltv, docs):
        approvals.raise_request(rid, "uw-01", repo=repo)
    with pytest.raises(InsufficientAuthority):
        approvals.approve(ltv, "su-01", repo=repo)
    assert approvals.approve(ltv, "cc-01", repo=repo)["status"] == "approved"
    assert approvals.approve(docs, "su-01", repo=repo)["status"] == "approved"


def test_nan_argument_denial_writes_its_policy_event(repo):
    raw = (
        '{"principal": NaN, "product_code": "FX5-C", "amortization_years": 25,'
        ' "payment_frequency": "monthly"}'
    )
    out = check_and_run({"name": "calculate_payment", "arguments": raw}, make_ctx(repo=repo))
    assert (out.status, out.reason) == ("denied", "invalid_arguments")
    rows = sql(
        "SELECT tool, decision, reason, reached_executor,"
        " args_digest->'principal' ? 'sha256', args_digest->>'amortization_years'"
        " FROM agent.policy_events"
    )
    assert rows == [("calculate_payment", "deny", "invalid_arguments", False, True, "25")]


def test_every_call_is_logged(repo):
    draft(repo)
    rows = sql("SELECT tool, decision, reached_executor FROM agent.policy_events")
    assert rows == [("prepare_exception_request", "draft", False)]
