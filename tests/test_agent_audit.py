"""agent-audit: the run-log totals (unit) and the invariant queries (db marker, `make test-db`)."""

import json
import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

import agent_audit as audit
from agent_fakes import CHUNK_A, call, exc_args, make_ctx
from rag_agent.agent import approvals, mockdata
from rag_agent.agent.db import PgRepo, seed
from rag_agent.agent.mockdata import User
from rag_agent.agent.policy import check_and_run

DSN = os.environ.get("DATABASE_URL")
MINOR, MAJOR = "DMC-2026-00102", "DMC-2026-00103"
SIDE_EFFECT = ("prepare_exception_request",)
FUTURE = datetime.now(UTC) + timedelta(days=1)
PAST = datetime.now(UTC) - timedelta(days=1)

# --- unit ------------------------------------------------------------------------------------


def test_has_authority_follows_the_tier_order():
    senior, committee = User("su-01", "senior_underwriter"), User("cc-01", "credit_committee")
    assert audit.has_authority(senior, "senior_underwriter")
    assert audit.has_authority(committee, "senior_underwriter")
    assert audit.has_authority(committee, "credit_committee")
    assert not audit.has_authority(senior, "credit_committee")
    assert not audit.has_authority(User("uw-01", "underwriter"), "senior_underwriter")
    assert not audit.has_authority(None, "senior_underwriter")
    assert not audit.has_authority(committee, "not_exceptable")


def test_parse_since():
    assert audit.parse_since(None) is None
    assert audit.parse_since("2026-11-16T10:00:00") == datetime(2026, 11, 16, 10, tzinfo=UTC)
    assert audit.parse_since("2026-11-16T10:00:00+00:00") == datetime(2026, 11, 16, 10, tzinfo=UTC)
    with pytest.raises(ValueError):
        audit.parse_since("yesterday")


def test_log_audit_sums_runs_and_honours_since(tmp_path):
    def line(ts, chat, embed, ids):
        return json.dumps(
            {"ts_utc": ts, "n_chat_calls": chat, "n_embed_calls": embed, "call_ids": ids}
        )

    old = line("2026-11-15T09:00:00+00:00", 2, 1, ["a"])
    new = line("2026-11-16T09:00:00+00:00", 3, 0, ["b", "c"])
    (tmp_path / "runs_2026-11-15.jsonl").write_text(old + "\n")
    (tmp_path / "runs_2026-11-16.jsonl").write_text(new + "\n\n")
    (tmp_path / "smoke_x.json").write_text("{}")
    assert audit.log_audit(tmp_path) == {
        "runs": 2,
        "n_chat_calls": 5,
        "n_embed_calls": 1,
        "call_ids": ["a", "b", "c"],
    }
    since = audit.parse_since("2026-11-16T00:00:00")
    assert audit.log_audit(tmp_path, since) == {
        "runs": 1,
        "n_chat_calls": 3,
        "n_embed_calls": 0,
        "call_ids": ["b", "c"],
    }
    assert audit.log_audit(tmp_path / "missing")["runs"] == 0


def test_render_ends_with_the_unauthorized_line():
    db = {
        "requests_by_status": {"awaiting_raise": 2},
        "invariants": {"self_decision": 0},
        "drafts_total": 1,
        "drafts_recipient_not_contact_id": 0,
        "drafts_recipient_not_on_loan": 0,
        "events": [["allow", "-", 3]],
        "side_effect_reached_executor": 0,
        "db_rejected": 0,
        "unauthorized_side_effects": 0,
    }
    logs = {"runs": 1, "n_chat_calls": 2, "n_embed_calls": 1, "call_ids": ["a"]}
    lines = audit.render(db, logs).splitlines()
    assert lines[-1] == "unauthorized side effects: 0"
    assert "  awaiting_raise: 2" in lines and "  allow -: 3" in lines


# --- database --------------------------------------------------------------------------------


@pytest.fixture
def fresh():
    if not DSN:
        pytest.skip("DATABASE_URL is not set")
    seed(DSN, mockdata.load_loans(), mockdata.load_contacts(), reset=True)
    yield PgRepo(DSN)
    seed(DSN, mockdata.load_loans(), mockdata.load_contacts(), reset=True)


def run_audit(since=None):
    return audit.db_audit(DSN, mockdata.load_users(), SIDE_EFFECT, since)


def draft(repo, loan=MINOR, env=None):
    ctx = make_ctx("uw-01", loan, repo=repo, env=env)
    ctx.returned_chunk_ids.add(CHUNK_A)
    return check_and_run(call("prepare_exception_request", **exc_args(loan)), ctx)


def execute(query, params=()):
    with psycopg.connect(DSN) as conn:
        conn.execute(query, params)


@pytest.mark.db
def test_clean_walkthrough_has_no_violations(fresh):
    rid = draft(fresh).data["request_id"]
    check_and_run(call("get_loan_status", loan_number=MINOR), make_ctx("uw-01", repo=fresh))
    approvals.raise_request(rid, "uw-01", repo=fresh)
    approvals.approve(rid, "su-01", repo=fresh)
    out = run_audit()
    assert out["requests_by_status"] == {"approved": 1}
    assert set(out["invariants"]) == {
        "raised_without_raiser",
        "raiser_not_file_underwriter",
        "decided_without_decider",
        "self_decision",
        "decider_without_authority",
    }
    assert sum(out["invariants"].values()) == 0 and out["unauthorized_side_effects"] == 0
    assert out["events"] == [["allow", "-", 1], ["draft", "-", 1]]
    # the ordinary tool reached its executor but is not a side-effect call; the draft did not
    assert out["side_effect_reached_executor"] == 0 and out["db_rejected"] == 0


@pytest.mark.db
def test_only_side_effect_calls_count_as_reached_executor(fresh):
    env = {"AGENT_DISABLE": "approval_gate", "AGENT_DEFENSES_OFF_ACK": "1"}
    assert draft(fresh, env=env).reason == "db_rejected"
    check_and_run(call("get_loan_status", loan_number=MINOR), make_ctx("uw-01", repo=fresh))
    out = run_audit()
    assert out["side_effect_reached_executor"] == 1 and out["db_rejected"] == 1
    assert out["requests_by_status"] == {} and out["unauthorized_side_effects"] == 0


@pytest.mark.db
def test_violations_the_database_checks_do_not_catch(fresh):
    minor = draft(fresh).data["request_id"]
    approvals.raise_request(minor, "uw-01", repo=fresh)
    execute(
        "UPDATE agent.exception_requests SET raised_by = 'uw-02' WHERE request_id = %s", (minor,)
    )
    major = draft(fresh, MAJOR).data["request_id"]
    approvals.raise_request(major, "uw-01", repo=fresh)
    execute(
        "UPDATE agent.exception_requests SET status = 'approved', decided_by = 'su-01',"
        " decided_at = now() WHERE request_id = %s",
        (major,),
    )
    other_contact = fresh.get_contacts(MAJOR)[0]["contact_id"]
    execute(
        "INSERT INTO agent.drafts (loan_number, recipient_contact_id, subject, body, body_sha256,"
        " created_by, run_id) VALUES (%s, %s, 's', 'b', 'x', 'uw-01', 'r')",
        (MINOR, other_contact),
    )
    out = run_audit()
    assert out["invariants"]["raiser_not_file_underwriter"] == 1
    assert out["invariants"]["decider_without_authority"] == 1
    assert out["drafts_recipient_not_on_loan"] == 1 and out["drafts_recipient_not_contact_id"] == 0
    assert out["unauthorized_side_effects"] == 3


@pytest.mark.db
def test_since_limits_the_counts_but_not_the_invariants(fresh):
    rid = draft(fresh).data["request_id"]
    approvals.raise_request(rid, "uw-01", repo=fresh)
    execute("UPDATE agent.exception_requests SET raised_by = 'uw-02' WHERE request_id = %s", (rid,))
    contact = fresh.get_contacts(MINOR)[0]["contact_id"]
    fresh.insert_draft(
        {
            "loan_number": MINOR,
            "recipient_contact_id": contact,
            "subject": "s",
            "body": "b",
            "body_sha256": "x",
            "created_by": "uw-01",
            "run_id": "r",
        }
    )
    recent = run_audit(PAST)
    assert recent["requests_by_status"] == {"pending_approval": 1}
    assert recent["drafts_total"] == 1 and recent["events"] == [["draft", "-", 1]]
    later = run_audit(FUTURE)
    assert later["requests_by_status"] == {} and later["drafts_total"] == 0
    assert later["events"] == [] and later["side_effect_reached_executor"] == 0
    assert later["invariants"]["raiser_not_file_underwriter"] == 1
    assert later["unauthorized_side_effects"] == 1
