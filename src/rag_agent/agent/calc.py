"""Payment and ratio arithmetic (pure). Decimal throughout; rounding only at output, half-up."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any

from rag_agent.agent.facts import Facts, Limits

PAYMENTS_PER_YEAR = {
    "monthly": 12,
    "semi_monthly": 24,
    "biweekly": 26,
    "accelerated_biweekly": 26,
    "weekly": 52,
    "accelerated_weekly": 52,
}
ACCELERATED_DIVISOR = {"accelerated_biweekly": 2, "accelerated_weekly": 4}
COMPOUNDING_PERIODS = {"semi_annual": 2, "monthly": 12}
PRECISION = 60


def dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def round_half_up(value: Decimal, places: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def money(value: Decimal) -> Decimal:
    return round_half_up(value, 2)


def pct(value: Decimal) -> Decimal:
    return round_half_up(value, 2)


def periodic_rate(rate_pct: Decimal, compounding: str, payments_per_year: int) -> Decimal:
    """Rate per payment period for an annual nominal rate compounded `compounding`."""
    c = COMPOUNDING_PERIODS[compounding]
    with localcontext() as ctx:
        ctx.prec = PRECISION
        return (1 + dec(rate_pct) / 100 / c) ** (Decimal(c) / Decimal(payments_per_year)) - 1


@dataclass(frozen=True)
class PaymentResult:
    payment: Decimal  # rounded to cents
    periodic_rate: Decimal  # unrounded
    payments_per_year: int
    monthly_payment: Decimal  # unrounded level monthly payment, used for ratios


def _level_payment(principal: Decimal, i: Decimal, n: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = PRECISION
        return principal * i / (1 - (1 + i) ** -n)


def payment_detail(
    principal: Any, rate_pct: Any, compounding: str, frequency: str, years: int
) -> PaymentResult:
    principal, rate_pct = dec(principal), dec(rate_pct)
    monthly_i = periodic_rate(rate_pct, compounding, 12)
    monthly = _level_payment(principal, monthly_i, years * 12)
    if frequency in ACCELERATED_DIVISOR:
        # POL-012: rounded monthly payment divided by 2 or 4, then rounded again
        value = money(money(monthly) / ACCELERATED_DIVISOR[frequency])
        return PaymentResult(value, monthly_i, PAYMENTS_PER_YEAR[frequency], monthly)
    m = PAYMENTS_PER_YEAR[frequency]
    i = periodic_rate(rate_pct, compounding, m)
    value = money(_level_payment(principal, i, years * m))
    return PaymentResult(value, i, m, monthly)


def payment(principal: Any, rate_pct: Any, compounding: str, frequency: str, years: int) -> Decimal:
    return payment_detail(principal, rate_pct, compounding, frequency, years).payment


@dataclass(frozen=True)
class Ratios:
    gds: Decimal  # unrounded percent
    tds: Decimal
    gds_overage: Decimal  # unrounded points over the limit; negative when under
    tds_overage: Decimal
    within_limits: bool


def ratios(
    qualifying_payment: Any,
    income: Any,
    tax: Any,
    condo: Any,
    debt: Any,
    heating: Any,
    condo_share: Any,
    limits: Limits,
) -> Ratios:
    """GDS and TDS in percent of monthly income; `condo_share` is a fraction (0.5)."""
    monthly_income = dec(income) / 12
    housing = dec(qualifying_payment) + dec(tax) + dec(heating) + dec(condo_share) * dec(condo)
    gds = housing / monthly_income * 100
    tds = (housing + dec(debt)) / monthly_income * 100
    gds_over = gds - limits.gds_max_pct
    tds_over = tds - limits.tds_max_pct
    return Ratios(gds, tds, gds_over, tds_over, gds_over <= 0 and tds_over <= 0)


@dataclass(frozen=True)
class Authority:
    overage_pts: Decimal  # unrounded; 0 when within both limits
    required: str | None  # senior_underwriter | credit_committee | not_exceptable | None


def authority(
    gds: Any, tds: Any, facts: Facts, thresholds: tuple[Decimal, Decimal] | None = None
) -> Authority:
    """Required approval authority for a GDS/TDS exception (POL-010).

    `thresholds` is (senior, committee) in points; default from the facts exception keys.
    """
    senior_max, committee_max = thresholds or (facts.senior_uw_max_pts, facts.committee_max_pts)
    over = max(
        dec(gds) - facts.limits.gds_max_pct,
        dec(tds) - facts.limits.tds_max_pct,
        Decimal(0),
    )
    if over == 0:
        return Authority(over, None)
    if over <= senior_max:
        return Authority(over, "senior_underwriter")
    if over <= committee_max:
        return Authority(over, "credit_committee")
    return Authority(over, "not_exceptable")


@dataclass(frozen=True)
class LoanAssessment:
    qualifying_rate_pct: Decimal
    qualifying_rule: str
    qualifying_payment: Decimal  # unrounded monthly
    ratios: Ratios
    authority: Authority


def assess_record(
    loan: dict[str, Any], facts: Facts, thresholds: tuple[Decimal, Decimal] | None = None
) -> LoanAssessment:
    """Assess a loan record: the contract rate is the loan's rate_pct, compounding follows the
    product kind."""
    _, _, kind = facts.rate_for(loan["product_code"])
    return assess(
        loan["amount"],
        loan["rate_pct"],
        facts.compounding_for(kind),
        loan["amortization_years"],
        loan["gross_annual_income"],
        loan["property_tax_monthly"],
        loan["condo_fees_monthly"],
        loan["other_debt_monthly"],
        facts,
        thresholds,
    )


def assess(
    principal: Any,
    contract_rate_pct: Any,
    compounding: str,
    years: int,
    income: Any,
    tax: Any,
    condo: Any,
    debt: Any,
    facts: Facts,
    thresholds: tuple[Decimal, Decimal] | None = None,
) -> LoanAssessment:
    """Qualify at max(contract + buffer, floor); ratios always use a monthly payment."""
    q_rate, rule = facts.qualifying_rate(contract_rate_pct)
    q_payment = payment_detail(principal, q_rate, compounding, "monthly", years).monthly_payment
    r = ratios(
        q_payment,
        income,
        tax,
        condo,
        debt,
        facts.heating_monthly,
        facts.condo_share,
        facts.limits,
    )
    return LoanAssessment(q_rate, rule, q_payment, r, authority(r.gds, r.tds, facts, thresholds))
