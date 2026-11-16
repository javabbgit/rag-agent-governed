"""Policy file validation, defense switches and the data hygiene checks over data/mock."""

import hashlib
import re
import tomllib
from decimal import Decimal
from pathlib import Path

import pytest

from rag_agent.agent import calc, mockdata
from rag_agent.agent.errors import DefensesOffUnacknowledged, PolicyInvalid
from rag_agent.agent.facts import load_facts
from rag_agent.agent.policy_config import POLICY_PATH, load_policy

ROOT = Path(__file__).resolve().parents[1]
FACTS = load_facts()


def policy_text() -> str:
    return POLICY_PATH.read_text(encoding="utf-8")


def write(tmp_path, text: str) -> Path:
    p = tmp_path / "agent_policy.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_loads_and_records_sha256():
    pol = load_policy(env={})
    assert pol.sha256 == hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest()
    assert all(pol.defenses.values())
    assert pol.roles["broker"].scope == "own_submitted"
    assert "prepare_exception_request" not in pol.roles["broker"].tools
    assert len(pol.roles["underwriter"].tools) == 5


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t + "\n[extra]\nx = 1\n",
        lambda t: t.replace("search_policy", "wire_money", 1),
        lambda t: t.replace("[roles.credit_committee]", "[roles.janitor]"),
        lambda t: t.replace("max_denials = 3", "max_denials = 3\nmax_pizzas = 1"),
        lambda t: t.replace("role_allowlist = true", "role_allowlist = false"),
    ],
)
def test_invalid_policy_files(tmp_path, mutate):
    with pytest.raises((PolicyInvalid, DefensesOffUnacknowledged)):
        load_policy(write(tmp_path, mutate(policy_text())), env={})


def test_missing_file_is_policy_invalid(tmp_path):
    with pytest.raises(PolicyInvalid):
        load_policy(tmp_path / "none.toml", env={})


def test_defense_off_requires_ack():
    with pytest.raises(DefensesOffUnacknowledged):
        load_policy(env={"AGENT_DISABLE": "approval_gate"})
    pol = load_policy(
        env={"AGENT_DISABLE": "approval_gate,scope_check", "AGENT_DEFENSES_OFF_ACK": "1"}
    )
    assert not pol.on("approval_gate") and not pol.on("scope_check") and pol.on("schema_validation")


def test_unknown_switch_name_is_invalid():
    with pytest.raises(PolicyInvalid):
        load_policy(env={"AGENT_DISABLE": "everything", "AGENT_DEFENSES_OFF_ACK": "1"})


def test_authority_stores_facts_key_names_not_numbers():
    data = tomllib.loads(policy_text())
    flat = str(data["authority"])
    assert "exceptions.senior_uw_max_pts" in flat and "exceptions.committee_max_pts" in flat
    assert not re.search(r"\d\.\d", flat)


# --- data hygiene -----------------------------------------------------------------------------

LOANS = mockdata.load_loans()
CONTACTS = mockdata.load_contacts()
USERS = mockdata.load_users()
PRODUCTS = {"FX1-O", "FX3-C", "FX5-C", "VR5-C", "RNT-4", "SEA-5"}


def test_no_email_or_long_digit_runs_or_non_ascii():
    for path in (ROOT / "data" / "mock").glob("*.toml"):
        text = path.read_text(encoding="utf-8")
        assert "@" not in text and text.isascii(), path.name
        assert not re.search(r"\d{9,}", text), path.name


def test_id_formats_and_products():
    for loan in LOANS:
        assert re.fullmatch(r"DMC-2026-001\d\d", loan["loan_number"])
        assert loan["product_code"] in PRODUCTS and loan["amortization_years"] <= 25
        assert re.fullmatch(r"B-\d{4}", loan["borrower_ref"])
        assert loan["broker_id"] in USERS and loan["underwriter_id"] in USERS
    assert [x["loan_number"] for x in LOANS] == [f"DMC-2026-00{i}" for i in range(101, 113)]
    for c in CONTACTS:
        assert re.fullmatch(r"CT-\d{4}", c["contact_id"])
        assert c["role_label"] in {"broker contact", "borrower"}


def test_contacts_belong_to_exactly_one_loan():
    ids = [c["contact_id"] for c in CONTACTS]
    assert len(ids) == len(set(ids))
    numbers = {x["loan_number"] for x in LOANS}
    per_loan = {n: 0 for n in numbers}
    for c in CONTACTS:
        per_loan[c["loan_number"]] += 1
    assert all(1 <= v <= 2 for v in per_loan.values())


def test_owner_split_and_stages():
    def count(key, who):
        return sum(1 for x in LOANS if x[key] == who)

    assert (count("underwriter_id", "uw-01"), count("underwriter_id", "uw-02")) == (6, 6)
    assert [count("broker_id", b) for b in ("br-01", "br-02", "br-03")] == [5, 4, 3]
    assert {x["stage"] for x in LOANS} == {
        "application_received",
        "conditionally_approved",
        "conditions_outstanding",
        "funded",
        "renewal_pending",
        "declined",
    }
    assert any(len(x["outstanding_conditions"]) >= 2 for x in LOANS)


def test_users_have_one_role_each():
    assert {u.role for u in USERS.values()} == {
        "broker",
        "underwriter",
        "senior_underwriter",
        "credit_committee",
    }
    assert len(USERS) == 8


def test_in_progress_rates_match_the_rate_sheet():
    for loan in LOANS:
        if loan["stage"] in mockdata.IN_PROGRESS_STAGES:
            rate, _, _ = FACTS.rate_for(loan["product_code"])
            assert Decimal(str(loan["rate_pct"])) == Decimal(str(rate)), loan["loan_number"]


def test_l_minor_and_l_major_bands_are_locked():
    def overage(num):
        loan = next(x for x in LOANS if x["loan_number"] == num)
        assert loan["underwriter_id"] == "uw-01"
        assert loan["stage"] in mockdata.IN_PROGRESS_STAGES
        return calc.assess_record(loan, FACTS).ratios.tds_overage  # unrounded

    senior, committee = FACTS.senior_uw_max_pts, FACTS.committee_max_pts
    assert 0 < overage("DMC-2026-00102") <= senior
    assert senior < overage("DMC-2026-00103") <= committee


def test_data_report_shows_the_overage_rounded_up():
    import agent_data_report
    from rag_agent.agent.facts import FACTS_PATH

    v1_rates = tomllib.loads(FACTS_PATH.read_text(encoding="utf-8"))["v1"]["rates"]
    for loan in LOANS:
        exact = calc.assess_record(loan, FACTS).authority.overage_pts
        shown = Decimal(agent_data_report.row_for(loan, FACTS, v1_rates)["overage"])
        assert shown == calc.overage_up(exact)
        assert 0 <= shown - exact < Decimal("0.01"), loan["loan_number"]


def test_a_loan_is_within_both_limits():
    assert any(
        calc.assess_record(x, FACTS).ratios.within_limits
        for x in LOANS
        if x["stage"] in mockdata.IN_PROGRESS_STAGES
    )


def test_ddl_puts_every_object_in_the_agent_schema():
    ddl = (ROOT / "db" / "agent_schema.sql").read_text(encoding="utf-8")
    assert "CREATE SCHEMA IF NOT EXISTS agent;" in ddl
    for kind in ("TABLE", "SEQUENCE"):
        for name in re.findall(rf"CREATE {kind} IF NOT EXISTS (\S+)", ddl):
            assert name.startswith("agent."), name
    assert re.search(r"ON agent\.exception_requests", ddl)
    assert (
        "CHECK (exception_type IN ('gds_tds_ratio', 'ltv', 'income_documents', 'rate_hold'))" in ddl
    )
