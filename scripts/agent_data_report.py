"""Per-loan report of the mock data (make agent-data-report). Reads the TOML files and
corpus/facts.toml only; no database, no network.
"""

from __future__ import annotations

import sys
import tomllib
from collections import Counter
from decimal import Decimal
from typing import Any

from rag_agent.agent import calc, mockdata
from rag_agent.agent.facts import FACTS_PATH, Facts, load_facts


def rate_source(loan: dict[str, Any], facts: Facts, v1_rates: dict[str, Any]) -> str:
    current, key, _ = facts.rate_for(loan["product_code"])
    if Decimal(str(loan["rate_pct"])) == current:
        return key
    stem = loan["product_code"].replace("-", "")
    if f"{stem}_pct" in v1_rates:
        v1 = Decimal(str(v1_rates[f"{stem}_pct"]))
    else:
        v1 = Decimal(str(v1_rates["prime_pct"])) + Decimal(str(v1_rates[f"{stem}_spread_pts"]))
    return "v1.rates" if Decimal(str(loan["rate_pct"])) == v1 else "not on a rate sheet"


def row_for(loan: dict[str, Any], facts: Facts, v1_rates: dict[str, Any]) -> dict[str, Any]:
    _, _, kind = facts.rate_for(loan["product_code"])
    comp = facts.compounding_for(kind)
    pay = calc.payment(
        loan["amount"],
        loan["rate_pct"],
        comp,
        loan["payment_frequency"],
        loan["amortization_years"],
    )
    a = calc.assess_record(loan, facts)
    return {
        "loan": loan["loan_number"],
        "broker": loan["broker_id"],
        "uw": loan["underwriter_id"],
        "stage": loan["stage"],
        "product": loan["product_code"],
        "rate": f"{loan['rate_pct']:.2f}",
        "rate_source": rate_source(loan, facts, v1_rates),
        "payment": f"{pay} {loan['payment_frequency']}",
        "q_rate": f"{calc.pct(a.qualifying_rate_pct)}",
        "gds": f"{calc.pct(a.ratios.gds)}",
        "tds": f"{calc.pct(a.ratios.tds)}",
        "overage": f"{calc.pct(a.authority.overage_pts)}",
        "authority": a.authority.required or "-",
        "over": a.authority.required is not None,
    }


def main() -> int:
    facts = load_facts()
    v1_rates = tomllib.loads(FACTS_PATH.read_text(encoding="utf-8"))["v1"]["rates"]
    loans = mockdata.load_loans()
    rows = [row_for(loan, facts, v1_rates) for loan in loans]
    for r in rows:
        print(
            f"{r['loan']} broker={r['broker']} uw={r['uw']} {r['stage']} {r['product']} "
            f"rate={r['rate']} ({r['rate_source']}) payment={r['payment']} "
            f"q_rate={r['q_rate']} GDS={r['gds']} TDS={r['tds']} overage={r['overage']} "
            f"authority={r['authority']}"
        )
    print()
    print(f"loans: {len(rows)}")
    print(
        "by stage: "
        + ", ".join(f"{k}={v}" for k, v in sorted(Counter(r["stage"] for r in rows).items()))
    )
    print(
        "by underwriter: "
        + ", ".join(f"{k}={v}" for k, v in sorted(Counter(r["uw"] for r in rows).items()))
    )
    print(
        "by broker: "
        + ", ".join(f"{k}={v}" for k, v in sorted(Counter(r["broker"] for r in rows).items()))
    )
    print(f"over a limit at the qualifying rate: {sum(r['over'] for r in rows)}")
    print(f"contacts: {len(mockdata.load_contacts())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
