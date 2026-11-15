"""Calculation examples C1-C14 and the authority examples P22-P24 (no network, no database)."""

from decimal import Decimal

import pytest

from agent_fakes import call, make_ctx
from rag_agent.agent import calc, tools
from rag_agent.agent.errors import FactsMissingKey, ToolArgumentError
from rag_agent.agent.facts import load_facts, parse_facts
from rag_agent.agent.policy import check_and_run

FACTS = load_facts()


def run(**args):
    ctx = make_ctx()
    return tools.calculate_payment(args, ctx)


def test_c1_to_c4_payment_and_frequencies():
    d = calc.payment_detail(400000, 5, "semi_annual", "monthly", 25)
    assert d.payment == Decimal("2326.42")
    assert calc.round_half_up(d.periodic_rate, 10) == Decimal("0.0041239155")
    assert calc.payment(400000, 5, "semi_annual", "biweekly", 25) == Decimal("1072.54")
    assert calc.payment(400000, 5, "semi_annual", "accelerated_biweekly", 25) == Decimal("1163.21")
    assert calc.payment(400000, 5, "monthly", "monthly", 25) == Decimal("2338.36")


def test_c5_c6_c14_ratios():
    q_pay = calc.payment(400000, 7, "semi_annual", "monthly", 25)
    assert q_pay == Decimal("2801.66")
    r = calc.ratios(q_pay, 120000, 300, 0, 600, 100, Decimal("0.5"), FACTS.limits)
    assert (calc.pct(r.gds), calc.pct(r.tds)) == (Decimal("32.02"), Decimal("38.02"))
    r = calc.ratios(q_pay, 120000, 300, 400, 600, 100, Decimal("0.5"), FACTS.limits)
    assert calc.pct(r.gds) == Decimal("34.02")
    r = calc.ratios(2400, 108000, 350, 400, 500, 100, Decimal("0.5"), FACTS.limits)
    assert (calc.pct(r.gds), calc.pct(r.tds)) == (Decimal("33.89"), Decimal("39.44"))


def test_c7_product_and_conflicting_compounding():
    with pytest.raises(ToolArgumentError):
        run(
            principal=400000,
            product_code="FX5-C",
            compounding="monthly",
            amortization_years=25,
            payment_frequency="monthly",
        )
    out = check_and_run(
        call(
            "calculate_payment",
            principal=400000,
            product_code="FX5-C",
            compounding="monthly",
            amortization_years=25,
            payment_frequency="monthly",
        ),
        make_ctx(),
    )
    assert (out.status, out.reason) == ("denied", "invalid_arguments")


def test_c8_fixed_product_from_rate_sheet():
    out = run(
        principal=400000,
        product_code="FX5-C",
        amortization_years=25,
        payment_frequency="monthly",
        gross_annual_income=120000,
    )
    assert out["rate_pct"] == 4.69 and out["rate_source"] == "rates.FX5C_pct"
    assert out["payment"] == 2256.34
    assert out["qualifying_rate_pct"] == 6.44 and out["qualifying_payment_monthly"] == 2664.76
    assert out["qualifying_rule"] == "contract_plus_buffer"


def test_c9_variable_product_is_prime_plus_spread_monthly_compounding():
    out = run(
        principal=400000,
        product_code="VR5-C",
        amortization_years=25,
        payment_frequency="monthly",
        gross_annual_income=120000,
    )
    assert out["rate_pct"] == 4.15 and out["compounding"] == "monthly"
    assert out["payment"] == 2144.62
    assert out["qualifying_rate_pct"] == 5.9 and out["qualifying_payment_monthly"] == 2552.81


def test_c10_explicit_low_rate_qualifies_at_the_floor():
    out = run(
        principal=400000,
        annual_rate_pct=3.5,
        compounding="semi_annual",
        amortization_years=25,
        payment_frequency="monthly",
        gross_annual_income=120000,
    )
    assert out["qualifying_rate_pct"] == 5.4 and out["qualifying_rule"] == "floor"
    assert out["qualifying_payment_monthly"] == 2418.33
    assert out["rate_source"] == "argument"


def test_c11_c12_overage_and_authority():
    base = {
        "principal": 400000,
        "product_code": "FX5-C",
        "amortization_years": 25,
        "payment_frequency": "monthly",
        "gross_annual_income": 120000,
        "property_tax_monthly": 350,
        "condo_fees_monthly": 400,
    }
    out = run(**base, other_debt_monthly=950)
    assert (out["gds_pct"], out["tds_pct"], out["tds_overage_pts"]) == (33.15, 42.65, 0.65)
    assert out["within_limits"] is False
    a = calc.assess_record(
        {
            "amount": 400000,
            "product_code": "FX5-C",
            "rate_pct": 4.69,
            "amortization_years": 25,
            "gross_annual_income": 120000,
            "property_tax_monthly": 350,
            "condo_fees_monthly": 400,
            "other_debt_monthly": 950,
        },
        FACTS,
    )
    assert a.authority.required == "senior_underwriter"
    out = run(**base, other_debt_monthly=1100)
    assert (out["tds_pct"], out["tds_overage_pts"]) == (44.15, 2.15)
    a = calc.assess_record(
        {
            "amount": 400000,
            "product_code": "FX5-C",
            "rate_pct": 4.69,
            "amortization_years": 25,
            "gross_annual_income": 120000,
            "property_tax_monthly": 350,
            "condo_fees_monthly": 400,
            "other_debt_monthly": 1100,
        },
        FACTS,
    )
    assert a.authority.required == "credit_committee"


def test_c13_accelerated_rounds_half_up():
    out = run(
        principal=400000,
        product_code="FX5-C",
        amortization_years=25,
        payment_frequency="accelerated_biweekly",
    )
    assert out["payment"] == 1128.17
    out = run(
        principal=400000,
        product_code="FX5-C",
        amortization_years=25,
        payment_frequency="accelerated_weekly",
    )
    assert out["payment"] == 564.09


def test_missing_rate_or_compounding_is_an_argument_error():
    with pytest.raises(ToolArgumentError):
        run(
            principal=400000,
            amortization_years=25,
            payment_frequency="monthly",
            compounding="monthly",
        )
    with pytest.raises(ToolArgumentError):
        run(principal=400000, annual_rate_pct=5, amortization_years=25, payment_frequency="monthly")


def test_p22_authority_senior():
    a = calc.authority(Decimal("36.1"), Decimal("39.4"), FACTS)
    assert calc.pct(a.overage_pts) == Decimal("1.10") and a.required == "senior_underwriter"


def test_p23_authority_committee():
    a = calc.authority(Decimal("30"), Decimal("45.0"), FACTS)
    assert calc.pct(a.overage_pts) == Decimal("3.00") and a.required == "credit_committee"


def test_p24_authority_not_exceptable():
    a = calc.authority(Decimal("39.5"), Decimal("30"), FACTS)
    assert calc.pct(a.overage_pts) == Decimal("4.50") and a.required == "not_exceptable"


def test_authority_boundaries_use_unrounded_values():
    limit = FACTS.limits.gds_max_pct
    assert calc.authority(limit, 0, FACTS).required is None
    assert (
        calc.authority(limit + FACTS.senior_uw_max_pts, 0, FACTS).required == "senior_underwriter"
    )
    just_over = limit + FACTS.senior_uw_max_pts + Decimal("0.001")
    assert calc.authority(just_over, 0, FACTS).required == "credit_committee"


def test_facts_missing_key_names_the_key():
    data = {
        "ratios": {
            "gds_max_pct": 35.0,
            "tds_max_pct": 42.0,
            "heating_monthly_cad": 100,
            "condo_fee_share_pct": 50.0,
        },
        "qualifying": {"buffer_pts": 1.75, "floor_pct": 5.4},
        "payment": {"fixed_compounding": "semi-annual", "variable_compounding": "monthly"},
        "rates": {"prime_pct": 4.7},
        "exceptions": {"senior_uw_max_pts": 2.0, "committee_max_pts": 4.0},
    }
    parse_facts(data)
    del data["exceptions"]["committee_max_pts"]
    with pytest.raises(FactsMissingKey, match="exceptions.committee_max_pts"):
        parse_facts(data)


def test_unknown_product_in_rate_sheet_is_a_missing_key():
    with pytest.raises(FactsMissingKey):
        FACTS.rate_for("ZZZ-9")
