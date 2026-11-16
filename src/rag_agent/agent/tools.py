"""The five tools. `execute` runs the four read/draft tools; the side-effect tool goes through
`create_draft` (approval gate on) or `execute_unguarded` (approval gate off) from the policy layer.

A tool returns a dict with "status" (ok | no_basis | error), an optional "reason" and its data.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any

from rag_agent.agent import calc
from rag_agent.agent.ctx import Ctx
from rag_agent.agent.errors import DbRejected, ToolArgumentError


def _f(value: Decimal, places: int = 2) -> float:
    return float(calc.round_half_up(value, places))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- search_policy ---------------------------------------------------------------------------


def search_policy(args: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    prep = ctx.prepare_fn(args["query"], ctx.runtime)
    term = prep.terminal
    if term is not None:
        if term.status == "fallback":
            return {"status": "no_basis", "reason": term.reason or "no_basis"}
        if term.status == "degraded":
            return {"status": "error", "reason": "reranker_unavailable"}
        if term.status in ("blocked", "error"):
            return {"status": "error", "reason": "gateway_error", "d6_reason": term.reason}
        if term.status == "rejected_input":
            return {"status": "error", "reason": "invalid_query"}
        return {"status": "error", "reason": "search_unavailable"}
    chunks = []
    for i, src in enumerate(prep.context, start=1):
        ctx.returned_chunk_ids.add(src.chunk_id)
        chunks.append(
            {
                "marker": ctx.marker_for(src.chunk_id),
                "chunk_id": src.chunk_id,
                "doc_id": src.doc_id,
                "title": src.title,
                "section": src.section,
                "score": round(src.score, 4) if src.score is not None else None,
                "text": prep.context_text[i],
            }
        )
    if not chunks:
        return {"status": "no_basis", "reason": "no_chunks"}
    return {"status": "ok", "chunks": chunks}


# --- get_loan_status -------------------------------------------------------------------------


def get_loan_status(args: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    loan = ctx.repo.get_loan(args["loan_number"])
    if loan is None:
        raise ToolArgumentError("unknown loan", "loan_number")
    contacts = [
        {"contact_id": c["contact_id"], "role_label": c["role_label"]}
        for c in ctx.repo.get_contacts(args["loan_number"])
    ]
    return {"status": "ok", **loan, "contacts": contacts}


# --- calculate_payment -----------------------------------------------------------------------


def calculate_payment(args: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    facts = ctx.facts
    code = args.get("product_code")
    kind = None
    sheet_rate = rate_source = None
    if code is not None:
        sheet_rate, rate_source, kind = facts.rate_for(code)

    if args.get("annual_rate_pct") is not None:
        rate, rate_source = calc.dec(args["annual_rate_pct"]), "argument"
    elif sheet_rate is not None:
        rate = sheet_rate
    else:
        raise ToolArgumentError("annual_rate_pct or product_code is required", "annual_rate_pct")

    compounding = args.get("compounding")
    if kind is not None:
        if compounding is not None and compounding != facts.compounding_for(kind):
            raise ToolArgumentError("compounding conflicts with the product", "compounding")
        compounding = facts.compounding_for(kind)
    elif compounding is None:
        raise ToolArgumentError("compounding or product_code is required", "compounding")

    try:
        years = int(args["amortization_years"])  # the schema also accepts a whole float (25.0)
    except (ValueError, OverflowError):
        raise ToolArgumentError(
            "amortization_years is not a whole number", "amortization_years"
        ) from None
    frequency = args["payment_frequency"]
    detail = calc.payment_detail(args["principal"], rate, compounding, frequency, years)
    out: dict[str, Any] = {
        "status": "ok",
        "principal": args["principal"],
        "rate_pct": _f(rate),
        "rate_source": rate_source,
        "compounding": compounding,
        "amortization_years": years,
        "payment_frequency": frequency,
        "payments_per_year": detail.payments_per_year,
        "payment": _f(detail.payment),
    }
    if frequency in calc.ACCELERATED_DIVISOR:
        # the payment is derived from the monthly payment, so there is no level payment count
        out["periodic_rate_monthly"] = _f(detail.periodic_rate, 10)
    else:
        out["n_payments"] = years * detail.payments_per_year
        out["periodic_rate"] = _f(detail.periodic_rate, 10)
    income = args.get("gross_annual_income")
    if income is not None:
        a = calc.assess(
            args["principal"],
            rate,
            compounding,
            years,
            income,
            args.get("property_tax_monthly", 0),
            args.get("condo_fees_monthly", 0),
            args.get("other_debt_monthly", 0),
            facts,
            thresholds(ctx),
        )
        out.update(
            {
                "qualifying_rate_pct": _f(a.qualifying_rate_pct),
                "qualifying_rule": a.qualifying_rule,
                "qualifying_payment_monthly": _f(calc.money(a.qualifying_payment)),
                "gds_pct": _f(a.ratios.gds),
                "tds_pct": _f(a.ratios.tds),
                "limits": {
                    "gds_max_pct": float(facts.limits.gds_max_pct),
                    "tds_max_pct": float(facts.limits.tds_max_pct),
                },
                "gds_overage_pts": float(calc.overage_up(a.ratios.gds_overage)),
                "tds_overage_pts": float(calc.overage_up(a.ratios.tds_overage)),
                "within_limits": a.ratios.within_limits,
            }
        )
    return out


# --- draft_reply -----------------------------------------------------------------------------


def draft_reply(args: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    draft_id = ctx.repo.insert_draft(
        {
            "loan_number": args["loan_number"],
            "recipient_contact_id": args["recipient_contact_id"],
            "subject": args["subject"],
            "body": args["body"],
            "body_sha256": _sha(args["body"]),
            "created_by": ctx.user_id,
            "run_id": ctx.run_id,
        }
    )
    ctx.draft_ids.append(draft_id)
    return {"status": "ok", "draft_id": draft_id, "draft_status": "draft"}


# --- prepare_exception_request ---------------------------------------------------------------


def thresholds(ctx: Ctx) -> tuple[Decimal, Decimal]:
    """(senior, committee) overage limits in points, resolved from the policy's facts keys."""
    return (
        ctx.facts.key_value(ctx.policy.senior_max_key),
        ctx.facts.key_value(ctx.policy.committee_max_key),
    )


def assess_loan(loan: dict[str, Any], ctx: Ctx) -> calc.LoanAssessment:
    """GDS/TDS of a loan record at the qualifying rate; the contract rate is the loan's rate."""
    return calc.assess_record(loan, ctx.facts, thresholds(ctx))


def _exception_row(args: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    loan = ctx.repo.get_loan(args["loan_number"])
    if loan is None:
        raise ToolArgumentError("unknown loan", "loan_number")
    gds = tds = overage = None
    if args["exception_type"] == "gds_tds_ratio":
        a = assess_loan(loan, ctx)
        if a.authority.required in (None, "not_exceptable"):
            raise ToolArgumentError("no exception is possible for this file", "exception_type")
        gds = calc.pct(a.ratios.gds)
        tds = calc.pct(a.ratios.tds)
        overage = calc.overage_up(a.authority.overage_pts)
        required = a.authority.required
    else:
        required = ctx.policy.minimum_role[args["exception_type"]]
    return {
        "loan_number": args["loan_number"],
        "exception_type": args["exception_type"],
        "requested_value": args["requested_value"],
        "justification": args["justification"],
        "justification_sha256": _sha(args["justification"]),
        "compensating_factors": list(args["compensating_factors"]),
        "supporting_chunk_ids": list(args["supporting_chunk_ids"]),
        "current_gds_pct": gds,
        "current_tds_pct": tds,
        "overage_pts": overage,
        "required_authority": required,
        "prepared_by": ctx.user_id,
        "run_id": ctx.run_id,
    }


def _exception_data(row: dict[str, Any], stored: dict[str, Any], already_open: bool) -> dict:
    overage = stored.get("overage_pts")
    return {
        "status": stored["status"],
        "request_id": stored["request_id"],
        "form": "EXC-100",
        "loan_number": row["loan_number"],
        "exception_type": row["exception_type"],
        "overage_pts": None if overage is None else float(overage),
        "required_authority": stored["required_authority"],
        "already_open": already_open,
    }


def create_draft(args: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    """Insert an EXC-100 as awaiting_raise, or return the open request. Never raises or approves."""
    row = _exception_row(args, ctx)
    stored, already_open = ctx.repo.create_exception_request(row)
    ctx.exception_drafts += 1
    return _exception_data(row, stored, already_open)


def execute_unguarded(args: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    """Used only with approval_gate off: insert as approved with no human. The database refuses."""
    row = _exception_row(args, ctx)
    try:
        stored = ctx.repo.insert_exception_unguarded(row)
    except DbRejected:
        return {"status": "error", "reason": "db_rejected"}
    ctx.exception_drafts += 1
    return _exception_data(row, stored, False)


_EXECUTORS = {
    "search_policy": search_policy,
    "get_loan_status": get_loan_status,
    "calculate_payment": calculate_payment,
    "draft_reply": draft_reply,
}


def execute(name: str, args: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    if name not in _EXECUTORS:
        raise ValueError("not an executable tool")
    return _EXECUTORS[name](args, ctx)
