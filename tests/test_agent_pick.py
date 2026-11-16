"""agent-pick rules and agent-prescan (fake client, no network)."""

import pytest

from agent_pick import PickError, main, pick
from rag_agent.agent import mockdata
from rag_agent.agent.facts import load_facts
from rag_agent.agent.policy_config import load_policy

USERS = mockdata.load_users()
LOANS = mockdata.load_loans()
FACTS = load_facts()
SCOPES = {role: p.scope for role, p in load_policy(env={}).roles.items()}


def do(who, kind=None):
    return pick(who, kind, USERS, LOANS, SCOPES, FACTS)


def test_first_visible_loan_by_number():
    assert do("uw-01") == "DMC-2026-00101"
    assert do("uw-02") == "DMC-2026-00107"
    assert do("br-03") == "DMC-2026-00104"
    assert do("su-01") == "DMC-2026-00101"


def test_kinds_pick_the_banded_loans():
    assert do("uw-01", "tds_minor") == "DMC-2026-00102"
    assert do("uw-01", "tds_major") == "DMC-2026-00103"
    assert do("su-01", "tds_minor") == "DMC-2026-00102"


def test_bands_use_the_unrounded_overage_and_the_facts_thresholds(monkeypatch):
    import types
    from decimal import Decimal

    import agent_pick

    senior, committee = FACTS.senior_uw_max_pts, FACTS.committee_max_pts
    tiny = Decimal("0.004")

    def band(over):
        fake = types.SimpleNamespace(ratios=types.SimpleNamespace(tds_overage=over))
        monkeypatch.setattr(agent_pick.calc, "assess_record", lambda loan, facts: fake)
        return (do("uw-01", "tds_minor") is not None, do("uw-01", "tds_major") is not None)

    assert band(Decimal(0)) == (False, False)
    assert band(tiny) == (True, False)  # above zero, although it rounds to 0.00
    assert band(senior) == (True, False)
    assert band(senior + tiny) == (False, True)  # never rounded down into the minor band
    assert band(committee) == (False, True)
    assert band(committee + tiny) == (False, False)


def test_none_found_and_bad_input():
    assert do("br-03", "tds_major") is None
    with pytest.raises(PickError):
        do("nobody")
    with pytest.raises(PickError):
        do("uw-01", "other")


def test_main_exit_codes(capsys):
    assert main(["uw-01", "--kind", "tds_minor"]) == 0
    assert capsys.readouterr().out.strip() == "DMC-2026-00102"
    assert main(["br-03", "--kind", "tds_major"]) == 1
