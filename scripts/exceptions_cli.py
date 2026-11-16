"""Human CLI for EXC-100 requests (make exceptions / raise / discard / approve / refuse).

Without --yes the five EXC-100 fields, the overage and the required authority are printed and
"y" is asked for. Each approvals error prints one line and exits 1.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import Any

import psycopg

from rag_agent.agent import approvals
from rag_agent.agent.db import Repo
from rag_agent.agent.errors import ApprovalError
from rag_agent.store import StoreError


def show(req: dict[str, Any]) -> str:
    over = "n/a" if req.get("overage_pts") is None else f"{req['overage_pts']:.2f}"
    return "\n".join(
        [
            f"{req['request_id']}  {req['form']}  loan {req['loan_number']}"
            f"  status {req['status']}",
            f"  exception_type:       {req['exception_type']}",
            f"  requested_value:      {req['requested_value']}",
            f"  justification:        {req['justification']}",
            f"  compensating_factors: {'; '.join(req['compensating_factors'])}",
            f"  Supporting sections:  {', '.join(req['supporting_chunk_ids'])}",
            f"  overage_pts:          {over}",
            f"  required_authority:   {req['required_authority']}",
        ]
    )


def _line(req: dict[str, Any]) -> str:
    return f"{req['request_id']}  {req['status']}  {req['loan_number']}  {req['exception_type']}"


def run(
    argv: list[str],
    repo: Repo | None = None,
    ask: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    lst = sub.add_parser("list")
    lst.add_argument("--status", choices=approvals.STATUSES)
    lst.add_argument("--ids-only", action="store_true")
    for name in ("raise", "discard", "approve", "refuse"):
        p = sub.add_parser(name)
        p.add_argument("id")
        p.add_argument("--as", dest="user", required=True)
        p.add_argument("--yes", action="store_true")
        if name in ("discard", "refuse"):
            p.add_argument("--reason", default="")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "list":
            for req in approvals.list_requests(args.status or None, repo=repo):
                out(req["request_id"] if args.ids_only else _line(req))
            return 0
        req = approvals.get_request(args.id, repo=repo)
        if not args.yes:
            out(show(req))
            if ask(f"{args.cmd} {args.id} as {args.user}? [y/N] ").strip().lower() != "y":
                out("not confirmed; nothing changed")
                return 1
        if args.cmd == "raise":
            done = approvals.raise_request(args.id, args.user, repo=repo)
        elif args.cmd == "discard":
            done = approvals.discard(args.id, args.user, args.reason, repo=repo)
        elif args.cmd == "approve":
            done = approvals.approve(args.id, args.user, repo=repo)
        else:
            done = approvals.refuse(args.id, args.user, args.reason, repo=repo)
    except ApprovalError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except (psycopg.Error, StoreError) as exc:
        print(f"database error: {type(exc).__name__}", file=sys.stderr)
        return 1
    out(f"{done['request_id']}: {done['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
