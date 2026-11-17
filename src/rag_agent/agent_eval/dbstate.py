"""Database state for agent evaluation runs: reset, per-task setup, and reading a run's drafts.

reset_business never restarts or sets agent.exception_request_seq or agent.draft_seq, so one id
names one request or draft across every run; it never touches agent.policy_events, agent.loans
or agent.contacts. Uses store.connect, as scripts/agent_audit.py does.
"""

from __future__ import annotations

import hashlib
from typing import Any

from psycopg.rows import dict_row

from rag_agent import store
from rag_agent.agent import calc, mockdata
from rag_agent.agent.db import REQUEST_INSERT_COLUMNS, Repo
from rag_agent.agent.facts import Facts
from rag_agent.agent.policy_config import AgentPolicy
from rag_agent.agent_eval.tasks import DRAFT_TOOL, EXCEPTABLE, Task

SETUP_RUN_ID = "setup"
SETUP_REQUESTED_VALUE = "Setup placeholder: approve at the current ratios."
SETUP_JUSTIFICATION = "Setup placeholder for an evaluation task. Not a real request."
SETUP_FACTORS = ["Setup placeholder"]


class SetupError(Exception):
    pass


def reset_business(dsn: str) -> None:
    """Empty exception requests and drafts. No RESTART IDENTITY, no sequence change."""
    with store.connect(dsn) as conn:
        conn.execute("TRUNCATE agent.exception_requests, agent.drafts")


def apply_setup(
    repo: Repo,
    task: Task,
    loans: dict[str, str],
    facts: Facts,
    policy: AgentPolicy,
    users: dict[str, mockdata.User] | None = None,
) -> dict[str, Any] | None:
    """Create the state a task's `setup` describes; None when the task has no setup.

    own_draft_open: one open gds_tds_ratio request on the loan, prepared by the task user.
    """
    if task.setup is None:
        return None
    if task.setup.get("kind") != "own_draft_open":
        raise SetupError(f"{task.id}: unknown setup kind")
    loan_number = loans[task.setup["loan"]]
    loan = repo.get_loan(loan_number)
    if loan is None:
        raise SetupError(f"{task.id}: setup loan not found")
    user = (users or mockdata.load_users())[task.user]
    role = policy.roles[user.role]
    if DRAFT_TOOL not in role.tools or not mockdata.visible_to(loan, user, role.scope):
        raise SetupError(f"{task.id}: the task user may not prepare a request on this loan")
    a = calc.assess_record(loan, facts)
    if a.authority.required not in EXCEPTABLE:
        raise SetupError(f"{task.id}: the setup loan needs no exception or cannot have one")
    row = {
        "loan_number": loan_number,
        "exception_type": "gds_tds_ratio",
        "requested_value": SETUP_REQUESTED_VALUE,
        "justification": SETUP_JUSTIFICATION,
        "justification_sha256": hashlib.sha256(SETUP_JUSTIFICATION.encode("utf-8")).hexdigest(),
        "compensating_factors": list(SETUP_FACTORS),
        "supporting_chunk_ids": [],
        "current_gds_pct": calc.pct(a.ratios.gds),
        "current_tds_pct": calc.pct(a.ratios.tds),
        "overage_pts": calc.overage_up(a.authority.overage_pts),
        "required_authority": a.authority.required,
        "prepared_by": task.user,
        "run_id": SETUP_RUN_ID,
    }
    assert set(row) == set(REQUEST_INSERT_COLUMNS)
    stored, already_open = repo.create_exception_request(row)
    if already_open:
        raise SetupError(f"{task.id}: an open request already exists; reset first")
    return stored


def drafts_for_run(dsn: str, run_id: str) -> list[dict[str, Any]]:
    """The draft rows of one run, each with the recipient's role_label."""
    with store.connect(dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT d.*, c.role_label FROM agent.drafts d"
            " LEFT JOIN agent.contacts c ON c.contact_id = d.recipient_contact_id"
            " WHERE d.run_id = %s ORDER BY d.draft_id",
            (run_id,),
        )
        return [dict(r) for r in cur.fetchall()]
