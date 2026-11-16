"""Print a loan number visible to a user (make agent-pick WHO=uw-01 [KIND=tds_minor|tds_major]).

Without KIND: the first visible loan by loan_number. With KIND: the first visible in-progress loan
whose unrounded TDS overage at the qualifying rate is above zero and at most
exceptions.senior_uw_max_pts (tds_minor), or above that and at most exceptions.committee_max_pts
(tds_major); both thresholds come from corpus/facts.toml. Exit 1 when there is none.
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from typing import Any

from rag_agent.agent import calc, mockdata
from rag_agent.agent.facts import Facts, load_facts
from rag_agent.agent.policy_config import load_policy

KINDS = ("tds_minor", "tds_major")


class PickError(Exception):
    pass


def _in_band(kind: str, over: Decimal, facts: Facts) -> bool:
    """Band of an unrounded TDS overage; the thresholds are the facts exception keys."""
    if kind == "tds_minor":
        return 0 < over <= facts.senior_uw_max_pts
    return facts.senior_uw_max_pts < over <= facts.committee_max_pts


def pick(
    who: str,
    kind: str | None,
    users: dict[str, mockdata.User],
    loans: list[dict[str, Any]],
    scopes: dict[str, str],
    facts: Facts,
) -> str | None:
    if who not in users:
        raise PickError("unknown user")
    if kind is not None and kind not in KINDS:
        raise PickError("unknown kind")
    user = users[who]
    for loan in sorted(loans, key=lambda r: r["loan_number"]):
        if not mockdata.visible_to(loan, user, scopes[user.role]):
            continue
        if kind is None:
            return loan["loan_number"]
        if loan["stage"] not in mockdata.IN_PROGRESS_STAGES:
            continue
        if _in_band(kind, calc.assess_record(loan, facts).ratios.tds_overage, facts):
            return loan["loan_number"]
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("who")
    ap.add_argument("--kind", choices=sorted(KINDS))
    args = ap.parse_args(argv)
    policy = load_policy(env={})
    scopes = {role: p.scope for role, p in policy.roles.items()}
    try:
        found = pick(
            args.who, args.kind, mockdata.load_users(), mockdata.load_loans(), scopes, load_facts()
        )
    except PickError as exc:
        print(f"agent-pick: {exc}", file=sys.stderr)
        return 1
    if found is None:
        print("agent-pick: no matching loan", file=sys.stderr)
        return 1
    print(found)
    return 0


if __name__ == "__main__":
    sys.exit(main())
