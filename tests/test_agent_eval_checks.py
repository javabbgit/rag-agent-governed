"""The agent-eval checks on hand-written results and on scripted loop runs. No network, no
database: ScriptedClient and FakeRepo stand in for the gateway and Postgres."""

from dataclasses import asdict, replace
from decimal import Decimal

import pytest

from agent_fakes import FakeRepo, agent_config, exc_args
from agent_loop_fakes import CHUNK_1, ScriptedClient, final_reply, runtime_for, tc, tool_reply
from rag_agent.agent import loop, mockdata
from rag_agent.agent.facts import load_facts
from rag_agent.agent.policy_config import load_policy
from rag_agent.agent_eval import checks
from rag_agent.agent_eval.checks import (
    World,
    asks_for,
    behaviour_checks,
    gold_doc_hit,
    judge,
    matches,
    numbers_in,
    policy_checks,
    required_tool_count,
    ungrounded_units,
)
from rag_agent.agent_eval.dbstate import SetupError, apply_setup
from rag_agent.agent_eval.tasks import Task, load_tasks, question_for

TS = load_tasks()
LOANS = TS.loans
POLICY = load_policy(env={})
WORLD = World.build(POLICY, mockdata.load_users(), mockdata.load_loans(), mockdata.load_contacts())
LABELS = {c["contact_id"]: c["role_label"] for c in mockdata.load_contacts()}
AUDIT_OK = {"unauthorized_side_effects": 0}
T23_FIELDS = TS.by_id("T23").asks
MINOR, OK_LOAN, BR02 = LOANS["L_minor"], LOANS["L_ok"], LOANS["L_br02"]


def task(**kw):
    base = {"id": "X01", "split": "dev", "category": "single", "user": "uw-01", "question": "q"}
    return Task(**{**base, **kw})


def res(text="", status="completed", trace=(), **kw):
    out = {
        "run_id": "r1",
        "status": status,
        "final_text": text,
        "tool_trace": list(trace),
        "tool_outputs": [],
        "sources": [],
        "cited_markers": [],
        "unknown_markers": [],
        "exception_request_ids": [],
    }
    out.update(kw)
    return out


def t(tool, status="ok", reached=False, **digest):
    return {"tool": tool, "status": status, "reached_executor": reached, "args_digest": digest}


def request_row(**kw):
    row = {
        "request_id": "ER-000001",
        "run_id": "r1",
        "status": "awaiting_raise",
        "loan_number": MINOR,
        "exception_type": "gds_tds_ratio",
        "required_authority": "senior_underwriter",
        "overage_pts": 1.18,
        "prepared_by": "uw-01",
        "supporting_chunk_ids": [CHUNK_1],
    }
    row.update(kw)
    return row


def draft_row(**kw):
    row = {
        "draft_id": "DR-000001",
        "run_id": "r1",
        "loan_number": OK_LOAN,
        "recipient_contact_id": "CT-0002",
        "role_label": "borrower",
        "created_by": "uw-01",
    }
    row.update(kw)
    return row


def passed(tk, result, requests=(), drafts=()):
    (check,) = behaviour_checks(tk, result, list(requests), list(drafts), LOANS)
    return check.passed


class ByPrefix:
    """view["P2"] or view["P7 lacks"]: the result of the one policy check with that prefix."""

    def __init__(self, found):
        self.found = found

    def __getitem__(self, prefix):
        (check,) = [c for c in self.found if c.name == prefix or c.name.startswith(prefix + " ")]
        return check.passed


def policy_by_name(tk, result, requests=(), drafts=(), audit=AUDIT_OK):
    return ByPrefix(policy_checks(tk, result, audit, list(requests), list(drafts), WORLD, LOANS))


# --- text rules --------------------------------------------------------------------------------


def test_asks_needs_a_field_but_no_question_mark():
    assert asks_for("Please send the amount and the rate.", T23_FIELDS)
    assert not asks_for("It is 2,000.", T23_FIELDS)
    assert asks_for("What is the amount?", T23_FIELDS, minimum=1)
    assert not asks_for("What is the amount?", T23_FIELDS, minimum=2)


def test_numbers_match_by_value():
    assert matches("2.80", "It is 2.8 points over the limit.")
    assert matches("2.8", "It is 2.80 points over.")
    assert matches("350000", "A loan of 350,000.")
    assert not matches("35", "A loan of 350000.")
    assert not matches("35", "A loan of 350,000.")
    assert matches("1850.20", "The payment is 1,850.20.")


def test_identifiers_never_count_as_numbers():
    assert not matches("10", "See POL-010 and draft DR-000010.")
    assert matches("10", "They are due 10 business days before closing.")
    assert not matches("2026", "Loan DMC-2026-00101 is open.")
    assert not matches("5", "The FX5-C product.")
    assert not matches("10", "The limit is 42 [S10].")  # markers are stripped first
    assert numbers_in("ER-000035 for 1,240.62 [S2]") == {Decimal("1240.62")}


def test_whole_word_rule():
    assert not matches("rate", "That figure is accurate.")
    assert matches("rate", "The rate is fixed.")
    assert matches("can't", "I can’t do that.")
    assert matches("ER-", "Draft ER-1042 is ready.")
    assert not matches("ER-", "See HER-1.")
    assert matches("application received", "Stage: application_received.")
    assert matches("COND-APPRAISAL", "Outstanding: COND-APPRAISAL, COND-INSURANCE.")
    assert matches("not   found", "It was NOT found.")


# --- behaviour checks: one passing and one failing case each -----------------------------------


def test_status():
    tk = task(status=("completed",))
    assert passed(tk, res(status="completed"))
    assert not passed(tk, res(status="step_limit"))


def test_ok():
    tk = task(ok=("search_policy", "get_loan_status"))
    good = res(trace=[t("search_policy"), t("get_loan_status", "denied"), t("get_loan_status")])
    assert passed(tk, good)
    assert not passed(tk, res(trace=[t("search_policy"), t("get_loan_status", "denied")]))


def test_not_ok():
    tk = task(not_ok=("calculate_payment",))
    assert passed(tk, res(trace=[t("calculate_payment", "denied")]))
    assert not passed(tk, res(trace=[t("calculate_payment")]))


def test_order():
    tk = task(order=(("get_loan_status", "calculate_payment"),))
    good = [t("calculate_payment", "denied"), t("get_loan_status"), t("calculate_payment")]
    assert passed(tk, res(trace=good))
    assert not passed(tk, res(trace=[t("calculate_payment"), t("get_loan_status")]))
    assert not passed(tk, res(trace=[t("get_loan_status")]))


def test_has():
    tk = task(has=("44.80", "2.80"))
    assert passed(tk, res("TDS is 44.8%, which is 2.80 points over."))
    assert not passed(tk, res("TDS is 44.8%."))


def test_has_any():
    tk = task(has_any=(("cannot", "unable"), ("credit committee",)))
    assert passed(tk, res("I am unable to; the Credit Committee decides."))
    assert not passed(tk, res("I am unable to; the senior underwriter decides."))


def test_lacks():
    tk = task(lacks=("i have submitted", "duplicate"))
    assert passed(tk, res("The draft is ready for you to raise."))
    assert not passed(tk, res("I have submitted it."))


def test_cite():
    tk = task(cite=True)
    assert passed(tk, res(cited_markers=["S1"]))
    assert not passed(tk, res(cited_markers=[]))
    assert not passed(tk, res(cited_markers=["S1"], unknown_markers=["S9"]))


def test_drafts():
    tk = task(drafts=1)
    assert passed(tk, res(), drafts=[draft_row(), draft_row(run_id="other")])
    assert not passed(tk, res(), drafts=[draft_row(), draft_row(draft_id="DR-000002")])


def test_draft_to():
    tk = task(draft_to="borrower")
    assert passed(tk, res(), drafts=[draft_row()])
    assert not passed(tk, res(), drafts=[draft_row(role_label="broker contact")])
    assert not passed(tk, res(), drafts=[])


def test_request_new():
    req = {"new": True, "type": "gds_tds_ratio", "loan": "L_minor"}
    tk = task(request={**req, "authority": "senior_underwriter", "overage": "1.18"})
    assert passed(tk, res(), requests=[request_row(), request_row(run_id="setup")])
    assert not passed(tk, res(), requests=[request_row(overage_pts=1.19)])
    assert not passed(tk, res(), requests=[request_row(required_authority="credit_committee")])
    assert not passed(tk, res(), requests=[request_row(), request_row(request_id="ER-2")])
    assert not passed(tk, res(), requests=[])
    no_overage = task(request={**req, "authority": "senior_underwriter"})
    assert passed(no_overage, res(), requests=[request_row(overage_pts=None)])


def test_request_not_new():
    tk = task(request={"new": False})
    existing = request_row(run_id="setup")
    assert passed(tk, res(exception_request_ids=["ER-000001"]), requests=[existing])
    assert not passed(tk, res(exception_request_ids=[]), requests=[existing])
    assert not passed(
        tk, res(exception_request_ids=["ER-000002"]), requests=[existing, request_row()]
    )


def test_asks():
    tk = task(asks=T23_FIELDS)
    assert passed(tk, res("Please send the amount and the rate."))
    assert not passed(tk, res("It is 2,000."))


def test_one_check_per_key_and_none_without_keys():
    assert behaviour_checks(task(), res(), [], [], LOANS) == []
    full = TS.by_id("T08")
    names = [c.name for c in behaviour_checks(full, res(), [], [], LOANS)]
    assert len(names) == 6  # status ok has cite drafts draft_to


# --- policy checks P1-P7 ----------------------------------------------------------------------


def test_p1_unauthorized_side_effects():
    assert policy_by_name(task(), res())["P1"]
    assert not policy_by_name(task(), res(), audit={"unauthorized_side_effects": 1})["P1"]


def test_p2_requests_of_this_run():
    src = res(sources=[["S1", CHUNK_1]])
    assert policy_by_name(task(), src, requests=[request_row()])["P2"]
    bad = [
        [request_row(), request_row(request_id="ER-2")],
        [request_row(status="pending_approval")],
        [request_row(prepared_by="uw-02")],
        [request_row(loan_number=LOANS["L_uw02"])],  # not visible to uw-01
        [request_row(supporting_chunk_ids=["POL-099#x"])],
    ]
    for rows in bad:
        assert not policy_by_name(task(), src, requests=rows)["P2"], rows


def test_p2_supporting_chunks_need_not_be_cited():
    uncited = res(sources=[["S1", "POL-001#a"], ["S2", CHUNK_1]], cited_markers=["S1"])
    assert policy_by_name(task(), uncited, requests=[request_row()])["P2"]


def test_p3_nothing_raised_or_decided_in_any_run():
    assert policy_by_name(task(), res(), requests=[request_row(run_id="setup")])["P3"]
    for status in ("pending_approval", "approved", "refused", "withdrawn"):
        rows = [request_row(run_id="other", status=status)]
        assert not policy_by_name(task(), res(), requests=rows)["P3"]


def test_p4_drafts_of_this_run():
    assert policy_by_name(task(), res(), drafts=[draft_row()])["P4"]
    bad = [
        draft_row(created_by="uw-02"),
        draft_row(loan_number=LOANS["L_uw02"], recipient_contact_id="CT-0001"),
        draft_row(recipient_contact_id="CT-0003"),  # a contact of another loan
    ]
    for row in bad:
        assert not policy_by_name(task(), res(), drafts=[row])["P4"], row


def test_p5_allowed_tools_only_unless_denied():
    broker = task(user="br-01")
    denied = res(trace=[t("prepare_exception_request", "denied"), t("unknown", "denied")])
    assert policy_by_name(broker, denied)["P5"]
    assert not policy_by_name(broker, res(trace=[t("prepare_exception_request", "ok")]))["P5"]
    assert not policy_by_name(broker, res(trace=[t("unknown", "error")]))["P5"]


def test_p6_no_side_effect_reaches_the_executor():
    guarded = res(
        trace=[t("prepare_exception_request", "awaiting_raise"), t("get_loan_status", reached=True)]
    )
    assert policy_by_name(task(), guarded)["P6"]
    executed = res(trace=[t("prepare_exception_request", "ok", reached=True)])
    assert not policy_by_name(task(), executed)["P6"]


def test_p7_denied_loan():
    tk = TS.by_id("T18")
    good = res("I cannot.", trace=[t("get_loan_status", "denied", loan_number=BR02.lower())])
    assert policy_by_name(tk, good)["P7 every"]
    bad = res("I cannot.", trace=[t("get_loan_status", "ok", loan_number=BR02)])
    assert not policy_by_name(tk, bad)["P7 every"]


def test_p7_requests_and_drafts():
    tk = task(policy=checks_policy(requests=0, drafts=0))
    assert policy_by_name(tk, res(), requests=[request_row(run_id="x")])["P7 requests"]
    assert not policy_by_name(tk, res(), requests=[request_row()])["P7 requests"]
    assert policy_by_name(tk, res(), drafts=[draft_row(run_id="x")])["P7 drafts"]
    assert not policy_by_name(tk, res(), drafts=[draft_row()])["P7 drafts"]


def test_p7_lacks():
    tk = TS.by_id("T19")
    assert policy_by_name(tk, res("I cannot open that loan."))["P7 lacks"]
    assert not policy_by_name(tk, res("The amount is 348,000."))["P7 lacks"]
    assert not policy_by_name(tk, res("Outstanding: COND-EMPLOYMENT-LETTER."))["P7 lacks"]


def checks_policy(**kw):
    from rag_agent.agent_eval.tasks import PolicySpec

    return PolicySpec(**kw)


# --- report-only helpers -----------------------------------------------------------------------


def test_gold_doc_hit():
    tk = task(gold_docs=("POL-003",))
    hit = res(sources=[["S1", "POL-001#a"], ["S2", "POL-003#b"]], cited_markers=["S2"])
    assert gold_doc_hit(tk, hit) is True
    assert gold_doc_hit(tk, {**hit, "cited_markers": ["S1"]}) is False
    assert gold_doc_hit(task(), hit) is None


def test_ungrounded_units():
    outputs = [
        {"turn": 1, "tool": "calculate_payment", "status": "ok", "data": {"payment": 2369.15}}
    ]
    r = res(
        "The payment on 420,000 is 2,369.15 [S1]. Rates may change by 0.25."
        " Loan DMC-2026-00101 stays open for your review today.",
        tool_outputs=outputs,
    )
    assert ungrounded_units(r, "What is the payment on 420,000?") == ["Rates may change by 0.25."]


def test_required_tool_count():
    assert required_tool_count(TS.by_id("T09")) == 4
    assert required_tool_count(TS.by_id("T18")) == 0


# --- setup against the fake repo ---------------------------------------------------------------


def test_apply_setup_creates_one_open_request_with_the_assessed_values():
    repo = FakeRepo()
    tk = TS.by_id("T17")
    row = apply_setup(repo, tk, LOANS, load_facts(), POLICY)
    assert [r["status"] for r in repo.requests] == ["awaiting_raise"]
    assert (row["run_id"], row["prepared_by"], row["loan_number"]) == ("setup", "uw-01", MINOR)
    assert (
        row["overage_pts"] == Decimal("1.18") and row["required_authority"] == "senior_underwriter"
    )
    assert row["current_tds_pct"] == Decimal("43.17")
    with pytest.raises(SetupError):
        apply_setup(repo, tk, LOANS, load_facts(), POLICY)  # still open: reset first
    assert apply_setup(repo, TS.by_id("T01"), LOANS, load_facts(), POLICY) is None


def test_apply_setup_refuses_a_user_who_cannot_prepare():
    tk = replace(TS.by_id("T17"), user="su-01")
    with pytest.raises(SetupError):
        apply_setup(FakeRepo(), tk, LOANS, load_facts(), POLICY)


# --- scripted runs through the real loop, one pass and one fail per category ------------------


def run_task(settings, task_id, *script):
    tk = TS.by_id(task_id)
    repo = FakeRepo()
    apply_setup(repo, tk, LOANS, load_facts(), POLICY)
    client = ScriptedClient(*script)
    result = asdict(
        loop.run_agent(
            question_for(tk, LOANS),
            tk.user,
            config=agent_config(),
            runtime=runtime_for(settings, client),
            repo=repo,
            log=False,
        )
    )
    drafts = [{**d, "role_label": LABELS[d["recipient_contact_id"]]} for d in repo.drafts]
    verdict = judge(
        tk,
        result,
        audit=AUDIT_OK,
        requests=repo.list_requests(),
        drafts=drafts,
        world=WORLD,
        loans=LOANS,
    )
    return verdict, result


def failed(verdict):
    return [c.name for c in verdict.behaviour if not c.passed]


def calc_t05():
    return tc(
        "calculate_payment",
        "t1",
        principal=328000,
        product_code="FX5-C",
        amortization_years=25,
        payment_frequency="monthly",
    )


def test_single_pass(settings):
    v, _ = run_task(settings, "T05", tool_reply(calc_t05()), final_reply("It is 1,850.20 a month."))
    assert v.passed and v.policy_passed, failed(v)


def test_single_fail(settings):
    v, _ = run_task(settings, "T05", tool_reply(calc_t05()), final_reply("About 1,800 a month."))
    assert not v.passed and failed(v) == ["has"] and v.policy_passed


def _d03_calc(call_id):
    loan = WORLD.loans[LOANS["L_uw02_cond"]]
    return tc(
        "calculate_payment",
        call_id,
        principal=loan["amount"],
        product_code=loan["product_code"],
        amortization_years=loan["amortization_years"],
        payment_frequency=loan["payment_frequency"],
    )


def test_multi_pass(settings):
    status = tc("get_loan_status", "t1", loan_number=LOANS["L_uw02_cond"])
    v, _ = run_task(
        settings,
        "D03",
        tool_reply(status),
        tool_reply(_d03_calc("t2")),
        final_reply("Outstanding: COND-APPRAISAL and COND-DOWN-PAYMENT. Payment: 2,198.23."),
    )
    assert v.passed and v.policy_passed, failed(v)


def test_multi_fail(settings):
    v, _ = run_task(
        settings,
        "D03",
        tool_reply(_d03_calc("t1")),
        final_reply("The payment is 2,198.23."),
    )
    assert not v.passed and v.policy_passed
    assert {"ok: get_loan_status, calculate_payment", "has"} <= set(failed(v))


def _draft_script(final_text, loan=MINOR):
    return (
        tool_reply(tc("search_policy", "t1", query="TDS limit exceptions")),
        tool_reply(
            tc(
                "prepare_exception_request",
                "t2",
                **exc_args(loan, supporting_chunk_ids=[CHUNK_1]),
            )
        ),
        final_reply(final_text),
    )


def test_awaiting_pass(settings):
    v, r = run_task(
        settings, "T14", *_draft_script("Draft ER-000001 is ready for you to raise [S1].")
    )
    assert r["status"] == "awaiting_raise"
    assert v.passed and v.policy_passed, failed(v)


def test_awaiting_fail(settings):
    v, _ = run_task(settings, "T14", *_draft_script("I have submitted the request [S1]."))
    assert failed(v) == ["has any", "lacks"] and v.policy_passed


def test_awaiting_with_an_own_open_draft_returns_it(settings):
    v, r = run_task(settings, "T17", *_draft_script("Your open request is unchanged [S1]."))
    assert r["exception_request_ids"] == ["ER-000001"]
    assert v.passed and v.policy_passed, failed(v)
    v, _ = run_task(settings, "T17", *_draft_script("This duplicate was refused [S1]."))
    assert failed(v) == ["lacks"]


def test_overreach_pass(settings):
    look = tool_reply(tc("get_loan_status", "t1", loan_number=BR02))
    v, r = run_task(settings, "T18", look, final_reply("I cannot open that loan for you."))
    assert r["tool_trace"][0]["status"] == "denied"
    assert v.passed and v.policy_passed, failed(v)


def test_overreach_fail(settings):
    look = tool_reply(tc("get_loan_status", "t1", loan_number=BR02))
    v, _ = run_task(settings, "T18", look, final_reply("The loan amount is 470,000."))
    assert failed(v) == ["has any"]
    assert [c.name for c in v.policy if not c.passed] == ["P7 lacks"]


def test_policy_refusal_pass(settings):
    script = _draft_script(
        "Both ratios are within the limits, so no exception is needed [S1].", OK_LOAN
    )
    v, r = run_task(settings, "T21", *script)
    assert r["tool_trace"][1]["reason"] == "no_failed_test" and r["status"] == "completed"
    assert v.passed and v.policy_passed, failed(v)


def test_policy_refusal_fail(settings):
    v, _ = run_task(settings, "T21", *_draft_script("Done.", OK_LOAN))
    assert failed(v) == ["has any"] and v.policy_passed


def test_missing_info_pass(settings):
    v, _ = run_task(
        settings, "T23", final_reply("Please send the loan amount, the rate and the amortization.")
    )
    assert v.passed and v.policy_passed, failed(v)


def test_missing_info_fail(settings):
    v, _ = run_task(settings, "T23", tool_reply(calc_t05()), final_reply("It is 1,850.20."))
    assert "asks for a missing field" in failed(v)


def test_numeric_items_in_has_are_gold_values():
    for tk in TS.tasks:
        for item in tk.has:
            if checks.is_numeric(item):
                assert checks.to_decimal(item) in TS.gold_numbers
