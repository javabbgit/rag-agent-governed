"""Check the agent tables, the three named CHECK constraints and row counts (make agent-db-check).

Exit 1 when a table or constraint is missing.
"""

from __future__ import annotations

import sys

import psycopg

from rag_agent import store
from rag_agent.agent.db import default_dsn

TABLES = ("loans", "contacts", "exception_requests", "drafts", "policy_events")
CONSTRAINTS = ("raised_needed", "decision_needs_human", "reason_needed")


def check(dsn: str) -> tuple[dict[str, int | None], dict[str, bool]]:
    """Row count per table (None when the table is missing) and presence per constraint."""
    with store.connect(dsn) as conn:
        present = {
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'agent'"
            ).fetchall()
        }
        counts: dict[str, int | None] = {}
        for table in TABLES:
            if table in present:
                row = conn.execute(f"SELECT count(*) FROM agent.{table}").fetchone()  # fixed names
                counts[table] = row[0] if row else 0
            else:
                counts[table] = None
        found = {
            r[0]
            for r in conn.execute(
                "SELECT conname FROM pg_constraint"
                " WHERE conrelid = to_regclass('agent.exception_requests')"
            ).fetchall()
        }
    return counts, {name: name in found for name in CONSTRAINTS}


def main() -> int:
    try:
        counts, constraints = check(default_dsn())
    except (psycopg.Error, store.StoreError) as exc:
        print(f"agent-db-check failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    ok = True
    for table, n in counts.items():
        print(f"table {table}: {'MISSING' if n is None else f'{n} rows'}")
        ok &= n is not None
    for name, there in constraints.items():
        print(f"constraint {name}: {'ok' if there else 'MISSING'}")
        ok &= there
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
