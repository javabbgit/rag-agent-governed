"""Database access for the agent tables. Imports store.connect; store.py itself is not changed.

`Repo` is the interface the tools, policy layer and approvals use. PgRepo implements it on
Postgres; unit tests use an in-memory fake with the same methods.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from rag_agent import store
from rag_agent.agent.errors import DbRejected

AGENT_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "db" / "agent_schema.sql"

LOAN_COLUMNS = (
    "loan_number",
    "product_code",
    "stage",
    "amount",
    "rate_pct",
    "amortization_years",
    "term_years",
    "payment_frequency",
    "gross_annual_income",
    "property_tax_monthly",
    "condo_fees_monthly",
    "other_debt_monthly",
    "outstanding_conditions",
    "borrower_ref",
    "broker_id",
    "underwriter_id",
)
CONTACT_COLUMNS = ("contact_id", "loan_number", "role_label")
REQUEST_INSERT_COLUMNS = (
    "loan_number",
    "exception_type",
    "requested_value",
    "justification",
    "justification_sha256",
    "compensating_factors",
    "supporting_chunk_ids",
    "current_gds_pct",
    "current_tds_pct",
    "overage_pts",
    "required_authority",
    "prepared_by",
    "run_id",
)
TRANSITION_COLUMNS = (
    "raised_by",
    "raised_at",
    "decided_by",
    "decided_at",
    "decided_reason",
)
OPEN_STATUSES = ("awaiting_raise", "pending_approval")


class Repo(Protocol):
    def get_loan(self, loan_number: str) -> dict[str, Any] | None: ...

    def get_contacts(self, loan_number: str) -> list[dict[str, Any]]: ...

    def insert_draft(self, row: dict[str, Any]) -> str: ...

    def create_exception_request(self, row: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Insert as awaiting_raise, or return the open request with True."""
        ...

    def insert_exception_unguarded(self, row: dict[str, Any]) -> dict[str, Any]:
        """Insert as approved with no raised_by or decided_by (raises DbRejected)."""
        ...

    def log_event(self, event: dict[str, Any]) -> None: ...

    def get_request(self, request_id: str) -> dict[str, Any] | None: ...

    def list_requests(self, status: str | None = None) -> list[dict[str, Any]]: ...

    def transition(
        self, request_id: str, from_status: str, to_status: str, fields: dict[str, Any]
    ) -> bool:
        """Set the status and fields if the row is still in from_status. approvals.py only."""
        ...


def default_dsn() -> str:
    """DATABASE_URL, else the settings value, else the built-in default."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    try:
        from rag_agent.config import load_settings

        return load_settings().database_url
    except Exception:  # settings may need keys the DB tools do not
        from rag_agent.config import Settings

        return str(Settings.model_fields["database_url"].default)


def _plain(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in row.items()}


class PgRepo:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or default_dsn()

    def _conn(self) -> psycopg.Connection[Any]:
        return store.connect(self.dsn)

    def _one(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return _plain(cur.fetchone())

    def _all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return [_plain(r) or {} for r in cur.fetchall()]

    def get_loan(self, loan_number: str) -> dict[str, Any] | None:
        cols = ", ".join(LOAN_COLUMNS)
        return self._one(f"SELECT {cols} FROM agent.loans WHERE loan_number = %s", (loan_number,))

    def get_contacts(self, loan_number: str) -> list[dict[str, Any]]:
        cols = ", ".join(CONTACT_COLUMNS)
        return self._all(
            f"SELECT {cols} FROM agent.contacts WHERE loan_number = %s ORDER BY contact_id",
            (loan_number,),
        )

    def insert_draft(self, row: dict[str, Any]) -> str:
        out = self._one(
            "INSERT INTO agent.drafts (loan_number, recipient_contact_id, subject, body,"
            " body_sha256, created_by, run_id, status)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, 'draft')"
            " RETURNING draft_id",
            (
                row["loan_number"],
                row["recipient_contact_id"],
                row["subject"],
                row["body"],
                row["body_sha256"],
                row["created_by"],
                row["run_id"],
            ),
        )
        assert out is not None
        return str(out["draft_id"])

    def create_exception_request(self, row: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        cols = ", ".join(REQUEST_INSERT_COLUMNS)
        marks = ", ".join(["%s"] * len(REQUEST_INSERT_COLUMNS))
        values = tuple(row[c] for c in REQUEST_INSERT_COLUMNS)
        created = self._one(
            f"INSERT INTO agent.exception_requests ({cols}) VALUES ({marks})"
            " ON CONFLICT (loan_number, exception_type)"
            " WHERE status IN ('awaiting_raise', 'pending_approval') DO NOTHING RETURNING *",
            values,
        )
        if created is not None:
            return created, False
        existing = self._one(
            "SELECT * FROM agent.exception_requests WHERE loan_number = %s AND exception_type = %s"
            " AND status IN ('awaiting_raise', 'pending_approval') ORDER BY prepared_at LIMIT 1",
            (row["loan_number"], row["exception_type"]),
        )
        assert existing is not None
        return existing, True

    def insert_exception_unguarded(self, row: dict[str, Any]) -> dict[str, Any]:
        cols = ", ".join((*REQUEST_INSERT_COLUMNS, "status"))
        marks = ", ".join(["%s"] * (len(REQUEST_INSERT_COLUMNS) + 1))
        values = (*(row[c] for c in REQUEST_INSERT_COLUMNS), "approved")
        try:
            out = self._one(
                f"INSERT INTO agent.exception_requests ({cols}) VALUES ({marks}) RETURNING *",
                values,
            )
        except psycopg.errors.CheckViolation:
            raise DbRejected("the database rejected the insert") from None
        assert out is not None
        return out

    def log_event(self, event: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO agent.policy_events (run_id, user_id, role, rule_stage, step, tool,"
                " decision, reason, reached_executor, defenses, args_digest)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    event["run_id"],
                    event["user_id"],
                    event["role"],
                    event["rule_stage"],
                    event["step"],
                    event["tool"],
                    event["decision"],
                    event["reason"],
                    event["reached_executor"],
                    Jsonb(event["defenses"]),
                    Jsonb(event["args_digest"]),
                ),
            )

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM agent.exception_requests WHERE request_id = %s", (request_id,)
        )

    def list_requests(self, status: str | None = None) -> list[dict[str, Any]]:
        if status is None:
            return self._all(
                "SELECT * FROM agent.exception_requests ORDER BY prepared_at, request_id"
            )
        return self._all(
            "SELECT * FROM agent.exception_requests WHERE status = %s"
            " ORDER BY prepared_at, request_id",
            (status,),
        )

    def transition(
        self, request_id: str, from_status: str, to_status: str, fields: dict[str, Any]
    ) -> bool:
        if set(fields) - set(TRANSITION_COLUMNS):
            raise ValueError("unexpected transition field")
        sets = ", ".join(["status = %s", *(f"{c} = %s" for c in fields)])
        with self._conn() as conn:
            cur = conn.execute(
                f"UPDATE agent.exception_requests SET {sets} WHERE request_id = %s AND status = %s",
                (to_status, *fields.values(), request_id, from_status),
            )
            return cur.rowcount == 1


def apply_schema(conn: psycopg.Connection[Any]) -> None:
    conn.execute(AGENT_SCHEMA_PATH.read_text(encoding="utf-8"))


def seed(
    dsn: str,
    loans: list[dict[str, Any]],
    contacts: list[dict[str, Any]],
    reset: bool = False,
) -> dict[str, int]:
    """Apply the DDL and upsert loans and contacts. Idempotent. reset empties the mutable tables
    and restarts their numbering."""
    with store.connect(dsn) as conn:
        apply_schema(conn)
        if reset:
            conn.execute(
                "TRUNCATE agent.exception_requests, agent.drafts, agent.policy_events"
                " RESTART IDENTITY"
            )
            conn.execute("ALTER SEQUENCE agent.exception_request_seq RESTART WITH 1")
            conn.execute("ALTER SEQUENCE agent.draft_seq RESTART WITH 1")
        loan_cols = ", ".join(LOAN_COLUMNS)
        loan_marks = ", ".join(["%s"] * len(LOAN_COLUMNS))
        loan_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in LOAN_COLUMNS[1:])
        for loan in loans:
            conn.execute(
                f"INSERT INTO agent.loans ({loan_cols}) VALUES ({loan_marks})"
                f" ON CONFLICT (loan_number) DO UPDATE SET {loan_set}",
                tuple(loan[c] for c in LOAN_COLUMNS),
            )
        c_cols = ", ".join(CONTACT_COLUMNS)
        c_marks = ", ".join(["%s"] * len(CONTACT_COLUMNS))
        c_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in CONTACT_COLUMNS[1:])
        for contact in contacts:
            conn.execute(
                f"INSERT INTO agent.contacts ({c_cols}) VALUES ({c_marks})"
                f" ON CONFLICT (contact_id) DO UPDATE SET {c_set}",
                tuple(contact.get(c) for c in CONTACT_COLUMNS),
            )
    return {"loans": len(loans), "contacts": len(contacts)}
