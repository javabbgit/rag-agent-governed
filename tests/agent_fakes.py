"""Fakes for the agent tests: an in-memory Repo, a fake `prepare`, and ctx builders."""

from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from rag_agent.agent import mockdata
from rag_agent.agent.boot import AgentConfig
from rag_agent.agent.ctx import Ctx
from rag_agent.agent.db import OPEN_STATUSES
from rag_agent.agent.errors import DbRejected
from rag_agent.agent.facts import load_facts
from rag_agent.agent.policy_config import load_policy
from rag_agent.agent.specs import load_specs
from rag_agent.answer import AnswerResult, Prepared, Source

CHUNK_A = "POL-003#gds-and-tds-limits"
CHUNK_B = "POL-010#exceptions-and-authority"
CHUNK_C = "POL-004#how-the-qualifying-rate-is-calculated"

# an in-progress test-fixture loan far over both limits (P20); not part of the mock data
OVER_LOAN = {
    "loan_number": "DMC-2026-09999",
    "product_code": "FX5-C",
    "stage": "application_received",
    "amount": 470000,
    "rate_pct": 4.69,
    "amortization_years": 25,
    "term_years": 5,
    "payment_frequency": "monthly",
    "gross_annual_income": 90000,
    "property_tax_monthly": 380,
    "condo_fees_monthly": 0,
    "other_debt_monthly": 1050,
    "outstanding_conditions": [],
    "borrower_ref": "B-0099",
    "broker_id": "br-01",
    "underwriter_id": "uw-01",
}


class FakeRepo:
    def __init__(self, extra_loans: list[dict[str, Any]] | None = None):
        loans = [*mockdata.load_loans(), *(extra_loans or [])]
        self.loans = {r["loan_number"]: r for r in loans}
        self.contacts = mockdata.load_contacts()
        self.drafts: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.tick = 0

    def get_loan(self, loan_number):
        row = self.loans.get(loan_number)
        return copy.deepcopy(row) if row else None

    def get_contacts(self, loan_number):
        return [dict(c) for c in self.contacts if c["loan_number"] == loan_number]

    def insert_draft(self, row):
        draft_id = f"DR-{len(self.drafts) + 1:06d}"
        self.drafts.append({"draft_id": draft_id, **row, "status": "draft"})
        return draft_id

    def _new_request(self, row, status):
        self.tick += 1
        req = {
            "request_id": f"ER-{len(self.requests) + 1:06d}",
            "form": "EXC-100",
            **row,
            "status": status,
            "prepared_at": datetime(2026, 11, 15, 12, 0, self.tick, tzinfo=UTC),
            "raised_by": None,
            "raised_at": None,
            "decided_by": None,
            "decided_at": None,
            "decided_reason": None,
        }
        self.requests.append(req)
        return dict(req)

    def create_exception_request(self, row):
        for req in self.requests:
            if (
                req["loan_number"] == row["loan_number"]
                and req["exception_type"] == row["exception_type"]
                and req["status"] in OPEN_STATUSES
            ):
                return dict(req), True
        return self._new_request(row, "awaiting_raise"), False

    def insert_exception_unguarded(self, row):
        raise DbRejected("the database rejected the insert")

    def log_event(self, event):
        self.events.append(event)

    def get_request(self, request_id):
        for req in self.requests:
            if req["request_id"] == request_id:
                return dict(req)
        return None

    def list_requests(self, status=None):
        rows = [r for r in self.requests if status is None or r["status"] == status]
        return [dict(r) for r in sorted(rows, key=lambda r: (r["prepared_at"], r["request_id"]))]

    def transition(self, request_id, from_status, to_status, fields):
        for req in self.requests:
            if req["request_id"] == request_id and req["status"] == from_status:
                req.update(fields)
                req["status"] = to_status
                return True
        return False

    def count(self, status: str) -> int:
        return sum(1 for r in self.requests if r["status"] == status)


def make_prepare(
    chunk_ids: tuple[str, ...] = (CHUNK_A, CHUNK_B), terminal: AnswerResult | None = None
) -> Callable[[str, Any], Prepared]:
    """A `prepare` that returns fixed chunks (or a terminal result) and records the queries."""
    seen: list[str] = []

    def prepare(query: str, rt: Any) -> Prepared:
        seen.append(query)
        prep = Prepared(question=query, question_sha256="x", terminal=terminal)
        if terminal is None:
            for i, chunk_id in enumerate(chunk_ids, start=1):
                doc_id = chunk_id.split("#")[0]
                prep.context.append(
                    Source(
                        f"S{i}", chunk_id, doc_id, f"Title {doc_id}", "Section", 0.91234567, False
                    )
                )
                prep.context_text[i] = f"Text of {chunk_id}."
        return prep

    prepare.seen = seen  # type: ignore[attr-defined]
    return prepare


def agent_config(env: dict[str, str] | None = None) -> AgentConfig:
    """Facts, policy and specs without the lock check (the repo lock is generated by a human)."""
    return AgentConfig(load_facts(), load_policy(env=env or {}), load_specs())


def make_ctx(
    user_id: str = "uw-01",
    user_text: str = "",
    repo: FakeRepo | None = None,
    prepare_fn: Callable[[str, Any], Prepared] | None = None,
    env: dict[str, str] | None = None,
) -> Ctx:
    cfg = agent_config(env)
    role = mockdata.load_users()[user_id].role
    return Ctx(
        user_id=user_id,
        role=role,
        user_text=user_text,
        repo=repo or FakeRepo(),
        policy=cfg.policy,
        facts=cfg.facts,
        schemas=cfg.schemas,
        prepare_fn=prepare_fn or make_prepare(),
    )


def call(name: str, **args: Any) -> dict[str, Any]:
    import json

    return {"name": name, "arguments": json.dumps(args)}


def exc_args(loan_number: str, **over: Any) -> dict[str, Any]:
    args = {
        "loan_number": loan_number,
        "exception_type": "gds_tds_ratio",
        "requested_value": "TDS up to the committee limit",
        "justification": "Stable income and a strong savings history support this file.",
        "compensating_factors": ["Low loan to value", "Long employment history"],
        "supporting_chunk_ids": [CHUNK_A],
    }
    args.update(over)
    return args
