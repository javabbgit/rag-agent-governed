"""eval/agent/tasks.toml loading and validation. No network, no database; the committed file is
only read, and every failing case edits a tmp_path copy."""

import hashlib

import pytest

import agent_eval as cli
from agent_pick import pick
from rag_agent.agent import mockdata
from rag_agent.agent.facts import load_facts
from rag_agent.agent.policy_config import load_policy
from rag_agent.agent_eval.tasks import (
    TASKS_PATH,
    TaskFileError,
    load_tasks,
    question_for,
    tasks_sha256,
)

TEXT = TASKS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ts():
    return load_tasks()


def variant(tmp_path, old, new):
    assert TEXT.count(old) >= 1, old
    path = tmp_path / "tasks.toml"
    path.write_text(TEXT.replace(old, new, 1), encoding="utf-8")
    return path


def error_of(path):
    with pytest.raises(TaskFileError) as info:
        load_tasks(path)
    return info.value


# --- the committed file ------------------------------------------------------------------------


def test_committed_file_loads_with_the_expected_counts(ts):
    assert len(ts.tasks) == 36
    assert sum(t.split == "dev" for t in ts.tasks) == 11
    assert sum(t.split == "test" for t in ts.tasks) == 25
    assert len(ts.selectors) == 12
    assert {t.category for t in ts.tasks} == {
        "single",
        "multi",
        "awaiting",
        "overreach",
        "policy_refusal",
        "missing_info",
    }


def test_every_selector_resolves_to_its_expect(ts):
    for name, sel in ts.selectors.items():
        assert ts.loans[name] == sel.expect, name


def test_minor_and_major_agree_with_agent_pick(ts):
    policy = load_policy(env={})
    scopes = {role: p.scope for role, p in policy.roles.items()}
    users, loans, facts = mockdata.load_users(), mockdata.load_loans(), load_facts()
    assert ts.loans["L_minor"] == pick("uw-01", "tds_minor", users, loans, scopes, facts)
    assert ts.loans["L_major"] == pick("uw-01", "tds_major", users, loans, scopes, facts)


def test_questions_format_with_the_resolved_loans(ts):
    for task in ts.tasks:
        q = question_for(task, ts.loans)
        assert "{" not in q and "}" not in q
    assert ts.loans["L_minor"] in question_for(ts.by_id("T14"), ts.loans)


def test_sha256_is_of_the_file_bytes(ts):
    expected = hashlib.sha256(TASKS_PATH.read_bytes()).hexdigest()
    assert ts.sha256 == tasks_sha256() == expected


def test_phrase_expansion_shapes(ts):
    t02, t23, t10, t14 = (ts.by_id(i) for i in ("T02", "T23", "T10", "T14"))
    assert len(t02.has_any) == 1 and t02.has_any[0] == tuple(ts.phrases["not_found"])
    assert len(t23.asks) == 3 and t23.asks[0] == ("amount", "amounts", "principal")
    assert t10.has_any == (("credit committee",),)
    assert t14.lacks == tuple(ts.phrases["claims"])
    t17 = ts.by_id("T17")
    assert t17.lacks == (*ts.phrases["claims"], "duplicate", "duplicates", "duplicated")
    assert t17.setup == {"kind": "own_draft_open", "loan": "L_minor"}
    assert ts.by_id("T19").policy.lacks == ("348000", "COND-EMPLOYMENT-LETTER")


def test_gold_numbers_include_selector_calc_and_facts_values(ts):
    from decimal import Decimal

    for value in ("29.35", "2369.15", "1850.20", "1145.06", "90", "120", "470000"):
        assert Decimal(value) in ts.gold_numbers


def test_check_command_prints_one_line_per_selector_and_task(capsys):
    assert cli.main(["check"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 12 + 36 + 1
    assert lines[-1] == "tasks: 36 (dev 11, test 25); selectors: 12; gold mismatches: 0"


def test_check_command_prints_only_the_first_error(tmp_path, capsys):
    path = variant(tmp_path, 'gds = "33.56"', 'gds = "33.57"')
    assert cli.main(["check", "--tasks", str(path)]) == 1
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and err[0].startswith("loans.L_minor.gold.gds: ")


# --- one failing case per rule -----------------------------------------------------------------

CASES = [
    # gold values checked against the code
    ("gold gds", 'gds = "33.56"', 'gds = "33.57"', "loans.L_minor.gold.gds"),
    ("gold payment", 'payment = "2369.15"', 'payment = "2369.16"', "loans.L_br01.gold.payment"),
    (
        "gold conditions",
        'conditions = ["COND-EMPLOYMENT-LETTER"]',
        'conditions = ["COND-APPRAISAL"]',
        "loans.L_uw02.gold.conditions",
    ),
    (
        "gold authority",
        'overage = "0.00", authority = "none" }',
        'overage = "0.00", authority = "senior_underwriter" }',
        "loans.L_ok.gold.authority",
    ),
    (
        "gold stage",
        'gold = { stage = "application_received", payment',
        'gold = { stage = "funded", payment',
        "loans.L_br01.gold.stage",
    ),
    ("gold amount", 'amount = "470000"', 'amount = "470001"', "loans.L_br02.gold.amount"),
    (
        "unknown gold key",
        'gold = { payment = "858.80" }',
        'gold = { colour = "x" }',
        "loans.L_uw02_acc.gold",
    ),
    # selector resolution
    (
        "wrong expect",
        'stage = "renewal_pending"\nexpect = "DMC-2026-00105"',
        'stage = "renewal_pending"\nexpect = "DMC-2026-00101"',
        "loans.L_renewal",
    ),
    (
        "no loan matches",
        'frequency = "accelerated_biweekly"',
        'frequency = "weekly"',
        "loans.L_uw02_acc",
    ),
    ("unknown band", 'band = "over_max"', 'band = "huge"', "loans.L_over"),
    (
        "unknown selector key",
        '[loans.L_br03]\nwho = "br-03"',
        '[loans.L_br03]\nwho = "br-03"\ncolour = "x"',
        "loans.L_br03",
    ),
    (
        "unknown selector user",
        '[loans.L_br03]\nwho = "br-03"',
        '[loans.L_br03]\nwho = "br-09"',
        "loans.L_br03",
    ),
    # calc and facts gold
    ("calc payment", 'payment = "1850.20"', 'payment = "1850.21"', "calc[T05]"),
    ("calc id names no task", 'id = "T05"\nprincipal', 'id = "T99"\nprincipal', "calc[T99]"),
    (
        "facts value",
        'key = "down_payment.savings_history_days"\nvalue = "90"',
        'key = "down_payment.savings_history_days"\nvalue = "91"',
        "facts[down_payment.savings_history_days]",
    ),
    (
        "facts key",
        'key = "process.renewal_notice_days"',
        'key = "process.renewal_days"',
        "facts[process.renewal_days]",
    ),
    # tasks
    ("unknown top-level key", "version = 1\n", "version = 1\nowner = 'x'\n", "file"),
    (
        "unknown task key",
        'gold_docs = ["POL-005"]',
        'gold_docs = ["POL-005"]\nweight = 2',
        "tasks[T01]",
    ),
    ("duplicate id", 'id = "T02"', 'id = "T01"', "tasks[T01]"),
    (
        "unknown split",
        'id = "T01"\nsplit = "test"',
        'id = "T01"\nsplit = "train"',
        "tasks[T01].split",
    ),
    (
        "unknown category",
        'id = "T01"\nsplit = "test"\ncategory = "single"',
        'id = "T01"\nsplit = "test"\ncategory = "chat"',
        "tasks[T01].category",
    ),
    (
        "unknown user",
        'user = "cc-01"\nquestion = "How many days',
        'user = "zz-99"\nquestion = "How many days',
        "tasks[T01].user",
    ),
    (
        "undefined placeholder",
        "What stage is loan {L_br01}",
        "What stage is loan {L_nope}",
        "tasks[T03].question",
    ),
    (
        "ok tool outside the role",
        'ok = ["get_loan_status"]\nhas = ["application received"',
        'ok = ["prepare_exception_request"]\nhas = ["application received"',
        "tasks[T03].ok",
    ),
    (
        "unknown tool",
        'not_ok = ["calculate_payment", "draft_reply"]',
        'not_ok = ["send_email"]',
        "tasks[T02].not_ok",
    ),
    ("unknown status", 'status = ["completed"]', 'status = ["done"]', "tasks[T01].status"),
    (
        "unknown draft_to",
        'draft_to = "broker contact"',
        'draft_to = "notary"',
        "tasks[T12].draft_to",
    ),
    (
        "unknown phrase list",
        'has_any = ["@not_found"]',
        'has_any = ["@nothing"]',
        "tasks[T02].has_any",
    ),
    (
        "unknown phrase list in lacks",
        'lacks = ["@claims"]',
        'lacks = ["@claimz"]',
        "tasks[T14].lacks",
    ),
    (
        "bare string in has_any",
        'has_any = ["@not_found"]',
        'has_any = ["not found"]',
        "tasks[T02].has_any",
    ),
    ("numeric has without gold", 'has = ["90"]', 'has = ["91"]', "tasks[T01].has"),
    (
        "numeric policy.lacks without gold",
        'lacks = ["470000"]',
        'lacks = ["470001"]',
        "tasks[T18].policy.lacks",
    ),
    (
        "request overage without gold",
        'overage = "1.18" }',
        'overage = "1.19" }',
        "tasks[T14].request.overage",
    ),
    (
        "unknown request key",
        'request = { new = true, type = "gds_tds_ratio", loan = "L_minor"',
        'request = { new = true, urgency = "high", type = "gds_tds_ratio", loan = "L_minor"',
        "tasks[T14].request",
    ),
    (
        "unknown selector in request",
        'type = "gds_tds_ratio", loan = "L_minor"',
        'type = "gds_tds_ratio", loan = "L_nope"',
        "tasks[T14].request",
    ),
    (
        "unknown policy key",
        'policy = { denied_loan = "L_br02"',
        'policy = { blame = 1, denied_loan = "L_br02"',
        "tasks[T18].policy",
    ),
    (
        "unknown selector in policy",
        'policy = { denied_loan = "L_br02"',
        'policy = { denied_loan = "L_nope"',
        "tasks[T18].policy",
    ),
    (
        "unknown selector in setup",
        'setup = { kind = "own_draft_open", loan = "L_minor" }',
        'setup = { kind = "own_draft_open", loan = "L_nope" }',
        "tasks[T17].setup",
    ),
    ("cite false", 'has = ["90"]\ncite = true', 'has = ["90"]\ncite = false', "tasks[T01].cite"),
]


@pytest.mark.parametrize(("label", "old", "new", "where"), CASES, ids=[c[0] for c in CASES])
def test_each_rule_has_a_failing_case(tmp_path, label, old, new, where):
    err = error_of(variant(tmp_path, old, new))
    assert err.where == where, str(err)
    assert str(err).startswith(f"{where}: ")


# --- the three stricter validations, each on its own ------------------------------------------


def test_request_authority_is_cross_checked_against_the_code(tmp_path):
    ratio = variant(
        tmp_path,
        'loan = "L_minor", authority = "senior_underwriter"',
        'loan = "L_minor", authority = "credit_committee"',
    )
    err = error_of(ratio)
    assert err.where == "tasks[T14].request" and "senior_underwriter" in err.what
    other = variant(
        tmp_path,
        'type = "income_documents", loan = "L_ok", authority = "senior_underwriter"',
        'type = "income_documents", loan = "L_ok", authority = "credit_committee"',
    )
    assert error_of(other).where == "tasks[T16].request"


def test_setup_loan_must_be_visible_to_the_task_user(tmp_path):
    path = variant(
        tmp_path,
        'setup = { kind = "own_draft_open", loan = "L_minor" }',
        'setup = { kind = "own_draft_open", loan = "L_uw02" }',
    )
    err = error_of(path)
    assert err.where == "tasks[T17].setup" and "not visible" in err.what


def test_setup_loan_must_be_exceptable(tmp_path):
    path = variant(
        tmp_path,
        'setup = { kind = "own_draft_open", loan = "L_minor" }',
        'setup = { kind = "own_draft_open", loan = "L_ok" }',
    )
    err = error_of(path)
    assert err.where == "tasks[T17].setup" and "not exceptable" in err.what


def test_calc_id_must_name_a_task(tmp_path):
    err = error_of(variant(tmp_path, 'id = "T06"\nprincipal', 'id = "X06"\nprincipal'))
    assert err.where == "calc[X06]" and "names no task" in err.what
