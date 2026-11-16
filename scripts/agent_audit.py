"""Audit the agent tables and run logs (make agent-audit [SINCE=<ISO time>]). Read-only.

Prints exception requests by status, invariant violations, draft counts, policy events by
decision and reason, side-effect calls that reached the executor, and one line
"unauthorized side effects: N". Exit 1 unless N is 0. With SINCE the counts cover only rows at or
after it; the invariants are always checked over every row. Run-log totals and the call ids
(written to call_ids_<UTC>.txt) come from the agent log directory.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from rag_agent import store
from rag_agent.agent.db import default_dsn
from rag_agent.agent.mockdata import User, load_users
from rag_agent.agent.policy_config import APPROVER_ROLES, load_policy
from rag_agent.answer import REPO_ROOT
from rag_agent.config import Settings, load_settings

REQ = "agent.exception_requests"
DECIDED = "status IN ('approved', 'refused')"
# invariant name -> count query; each must return 0
INVARIANT_SQL = {
    "raised_without_raiser": f"SELECT count(*) FROM {REQ}"
    " WHERE status IN ('pending_approval', 'approved', 'refused') AND raised_by IS NULL",
    "raiser_not_file_underwriter": f"SELECT count(*) FROM {REQ} r"
    " JOIN agent.loans l USING (loan_number)"
    " WHERE r.raised_by IS NOT NULL AND r.raised_by <> l.underwriter_id",
    "decided_without_decider": f"SELECT count(*) FROM {REQ} WHERE {DECIDED} AND decided_by IS NULL",
    "self_decision": f"SELECT count(*) FROM {REQ} WHERE {DECIDED}"
    " AND (decided_by = prepared_by OR decided_by = raised_by)",
}
SINCE = "(%(since)s::timestamptz IS NULL OR {col} >= %(since)s)"


def has_authority(decider: User | None, required: str) -> bool:
    """Tier order is APPROVER_ROLES; a higher tier may decide what a lower one may."""
    if decider is None or decider.role not in APPROVER_ROLES or required not in APPROVER_ROLES:
        return False
    return APPROVER_ROLES.index(decider.role) >= APPROVER_ROLES.index(required)


def db_audit(
    dsn: str,
    users: dict[str, User],
    side_effect_tools: tuple[str, ...],
    since: datetime | None = None,
) -> dict[str, Any]:
    p = {"since": since, "tools": list(side_effect_tools)}
    with store.connect(dsn) as conn:

        def rows(sql: str) -> list[tuple[Any, ...]]:
            return conn.execute(sql, p).fetchall()

        def one(sql: str) -> int:
            return int(rows(sql)[0][0])

        invariants = {name: one(sql) for name, sql in INVARIANT_SQL.items()}
        decided = rows(
            f"SELECT decided_by, required_authority FROM {REQ}"
            f" WHERE {DECIDED} AND decided_by IS NOT NULL"
        )
        invariants["decider_without_authority"] = sum(
            1
            for decided_by, required in decided
            if not has_authority(users.get(decided_by), required)
        )
        req_since = (
            f"({SINCE.format(col='prepared_at')} OR"
            " (decided_at IS NOT NULL AND decided_at >= %(since)s))"
        )
        ev_since = SINCE.format(col="ts")
        d_since = SINCE.format(col="created_at")
        out: dict[str, Any] = {
            "requests_by_status": dict(
                rows(f"SELECT status, count(*) FROM {REQ} WHERE {req_since} GROUP BY status")
            ),
            "invariants": invariants,
            "drafts_total": one(f"SELECT count(*) FROM agent.drafts WHERE {d_since}"),
            "drafts_recipient_not_contact_id": one(
                "SELECT count(*) FROM agent.drafts"
                f" WHERE recipient_contact_id !~ '^CT-[0-9]{{4}}$' AND {d_since}"
            ),
            "drafts_recipient_not_on_loan": one(
                "SELECT count(*) FROM agent.drafts d WHERE NOT EXISTS ("
                "SELECT 1 FROM agent.contacts c WHERE c.contact_id = d.recipient_contact_id"
                " AND c.loan_number = d.loan_number)"
            ),
            "events": [
                list(r)
                for r in rows(
                    "SELECT decision, coalesce(reason, '-'), count(*) FROM agent.policy_events"
                    f" WHERE {ev_since} GROUP BY 1, 2 ORDER BY 1, 2"
                )
            ],
            "side_effect_reached_executor": one(
                "SELECT count(*) FROM agent.policy_events"
                f" WHERE tool = ANY(%(tools)s) AND reached_executor AND {ev_since}"
            ),
            "db_rejected": one(
                "SELECT count(*) FROM agent.policy_events"
                f" WHERE reason = 'db_rejected' AND {ev_since}"
            ),
        }
    out["unauthorized_side_effects"] = (
        sum(invariants.values()) + out["drafts_recipient_not_on_loan"]
    )
    return out


def parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    ts = datetime.fromisoformat(value)
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def log_audit(log_dir: str | Path, since: datetime | None = None) -> dict[str, Any]:
    """Totals over runs_*.jsonl lines at or after `since`."""
    d = Path(log_dir)
    if not d.is_absolute():
        d = REPO_ROOT / d
    out: dict[str, Any] = {"runs": 0, "n_chat_calls": 0, "n_embed_calls": 0, "call_ids": []}
    for path in sorted(d.glob("runs_*.jsonl")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            line = json.loads(raw)
            if since is not None and datetime.fromisoformat(line["ts_utc"]) < since:
                continue
            out["runs"] += 1
            out["n_chat_calls"] += line.get("n_chat_calls", 0)
            out["n_embed_calls"] += line.get("n_embed_calls", 0)
            out["call_ids"] += line.get("call_ids", [])
    return out


def default_log_dir() -> str:
    """AGENT_LOG_DIR, else the settings value, else the built-in default."""
    value = os.environ.get("AGENT_LOG_DIR")
    if value:
        return value
    try:
        return load_settings().agent_log_dir
    except Exception:  # settings may need keys the audit does not
        return str(Settings.model_fields["agent_log_dir"].default)


def render(db: dict[str, Any], logs: dict[str, Any]) -> str:
    lines = ["exception requests by status:"]
    lines += [f"  {s}: {n}" for s, n in sorted(db["requests_by_status"].items())] or ["  (none)"]
    lines += ["invariant violations (must be 0):"]
    lines += [f"  {name}: {n}" for name, n in db["invariants"].items()]
    lines += [
        f"drafts: {db['drafts_total']}",
        f"drafts, recipient not a CT-#### contact: {db['drafts_recipient_not_contact_id']}",
        f"drafts, recipient not a contact of the loan: {db['drafts_recipient_not_on_loan']}",
        "policy events by decision and reason:",
        *[f"  {decision} {reason}: {n}" for decision, reason, n in db["events"]],
        f"side-effect calls that reached the executor: {db['side_effect_reached_executor']}",
        f"db_rejected: {db['db_rejected']}",
        f"runs: {logs['runs']}  chat calls: {logs['n_chat_calls']}"
        f"  embed calls: {logs['n_embed_calls']}  call ids: {len(logs['call_ids'])}",
        f"unauthorized side effects: {db['unauthorized_side_effects']}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", help="ISO time; UTC when no offset is given")
    args = ap.parse_args(argv)
    try:
        since = parse_since(args.since)
    except ValueError:
        print("agent-audit: --since is not an ISO time", file=sys.stderr)
        return 2
    log_dir = default_log_dir()
    try:
        tools = load_policy(env={}).side_effect_tools
        db = db_audit(default_dsn(), load_users(), tools, since)
    except (psycopg.Error, store.StoreError) as exc:
        print(f"agent-audit failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    logs = log_audit(log_dir, since)
    out_dir = Path(log_dir) if Path(log_dir).is_absolute() else REPO_ROOT / log_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    ids_path = out_dir / f"call_ids_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.txt"
    ids_path.write_text("".join(f"{c}\n" for c in logs["call_ids"]), encoding="utf-8")
    print(render(db, logs))
    print(f"call ids: {ids_path}")
    return 0 if db["unauthorized_side_effects"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
