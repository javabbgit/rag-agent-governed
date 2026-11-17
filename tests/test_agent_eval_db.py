"""dbstate against the local pgvector (make test-db; run make agent-seed afterwards)."""

import os

import psycopg
import pytest

import agent_audit as audit
from agent_fakes import CHUNK_A
from rag_agent.agent import mockdata
from rag_agent.agent.db import PgRepo, seed
from rag_agent.agent.facts import load_facts
from rag_agent.agent.policy_config import load_policy
from rag_agent.agent_eval.dbstate import apply_setup, drafts_for_run, reset_business
from rag_agent.agent_eval.tasks import load_tasks

pytestmark = pytest.mark.db

DSN = os.environ.get("DATABASE_URL", "")
TS = load_tasks()
POLICY = load_policy(env={})
MINOR = TS.loans["L_minor"]
COUNTS = (
    "SELECT (SELECT count(*) FROM agent.policy_events), (SELECT count(*) FROM agent.loans),"
    " (SELECT count(*) FROM agent.contacts)"
)


@pytest.fixture
def fresh():
    if not DSN:
        pytest.skip("DATABASE_URL is not set")
    seed(DSN, mockdata.load_loans(), mockdata.load_contacts(), reset=True)
    yield PgRepo(DSN)
    seed(DSN, mockdata.load_loans(), mockdata.load_contacts(), reset=True)


def counts():
    with psycopg.connect(DSN) as conn:
        return conn.execute(COUNTS).fetchone()


def request_row(run_id="r1", loan=MINOR):
    return {
        "loan_number": loan,
        "exception_type": "income_documents",
        "requested_value": "Accept a later employment letter.",
        "justification": "Same employer for many years.",
        "justification_sha256": "0" * 64,
        "compensating_factors": ["Stable employment"],
        "supporting_chunk_ids": [CHUNK_A],
        "current_gds_pct": None,
        "current_tds_pct": None,
        "overage_pts": None,
        "required_authority": "senior_underwriter",
        "prepared_by": "uw-01",
        "run_id": run_id,
    }


def event(run_id="r1"):
    return {
        "run_id": run_id,
        "user_id": "uw-01",
        "role": "underwriter",
        "rule_stage": 9,
        "step": 1,
        "tool": "get_loan_status",
        "decision": "allow",
        "reason": None,
        "reached_executor": True,
        "defenses": {},
        "args_digest": {"loan_number": MINOR},
    }


def number(request_id):
    return int(request_id.split("-")[1])


def test_reset_business_leaves_events_loans_and_contacts(fresh):
    fresh.log_event(event())
    fresh.create_exception_request(request_row())
    fresh.insert_draft(
        {
            "loan_number": MINOR,
            "recipient_contact_id": "CT-0003",
            "subject": "s",
            "body": "b",
            "body_sha256": "0" * 64,
            "created_by": "uw-01",
            "run_id": "r1",
        }
    )
    before = counts()
    reset_business(DSN)
    assert counts() == before and before[0] == 1
    assert fresh.list_requests() == [] and drafts_for_run(DSN, "r1") == []


def test_ids_keep_increasing_across_resets(fresh):
    first, _ = fresh.create_exception_request(request_row("a"))
    reset_business(DSN)
    reset_business(DSN)
    second, _ = fresh.create_exception_request(request_row("b"))
    assert number(second["request_id"]) > number(first["request_id"])


def test_setup_after_reset_has_no_unauthorized_side_effects(fresh):
    reset_business(DSN)
    apply_setup(fresh, TS.by_id("T17"), TS.loans, load_facts(), POLICY)
    out = audit.db_audit(DSN, mockdata.load_users(), POLICY.side_effect_tools)
    assert out["unauthorized_side_effects"] == 0


def test_apply_setup_creates_one_awaiting_raise_row(fresh):
    reset_business(DSN)
    stored = apply_setup(fresh, TS.by_id("D06"), TS.loans, load_facts(), POLICY)
    rows = fresh.list_requests()
    assert len(rows) == 1 and rows[0]["request_id"] == stored["request_id"]
    assert (rows[0]["status"], rows[0]["run_id"]) == ("awaiting_raise", "setup")
    assert rows[0]["loan_number"] == TS.loans["L_major"]
    assert rows[0]["required_authority"] == "credit_committee"


def test_drafts_for_run_joins_the_role_label(fresh):
    draft_id = fresh.insert_draft(
        {
            "loan_number": MINOR,
            "recipient_contact_id": "CT-0003",
            "subject": "s",
            "body": "b",
            "body_sha256": "0" * 64,
            "created_by": "uw-01",
            "run_id": "r9",
        }
    )
    (row,) = drafts_for_run(DSN, "r9")
    assert (row["draft_id"], row["role_label"]) == (draft_id, "broker contact")
