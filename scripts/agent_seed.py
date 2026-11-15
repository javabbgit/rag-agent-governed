"""Apply db/agent_schema.sql and upsert the mock loans and contacts (make agent-seed [RESET=1]).

Idempotent. --reset also empties exception_requests, drafts and policy_events and restarts
their numbering. Local database only; no model calls.
"""

from __future__ import annotations

import argparse
import sys

import psycopg

from rag_agent.agent import mockdata
from rag_agent.agent.db import default_dsn, seed
from rag_agent.store import StoreError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args(argv)
    try:
        counts = seed(default_dsn(), mockdata.load_loans(), mockdata.load_contacts(), args.reset)
    except (psycopg.Error, StoreError) as exc:
        print(f"agent-seed failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(
        f"seeded {counts['loans']} loans and {counts['contacts']} contacts"
        + (" (tables reset)" if args.reset else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
