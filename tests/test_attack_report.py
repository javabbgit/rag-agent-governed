"""report.py and the rescore, compare and denials subcommands. Fakes only: no network or DB."""

import csv
import json
from datetime import UTC, datetime

import pytest

import attack_eval
from rag_agent.agent_attack import report as rpt
from rag_agent.agent_eval.tasks import load_tasks
from test_attack_runner import ATS, WORLD, go, make_env, malicious_trace, result

CLEAN = {"persisted": 0, "stopped_by_db": 0}
TASK = load_tasks().tasks[0]


def rec(attack="A01", repeat=1, **over):
    base = dict(
        attack=attack, channel="doc", repeat=repeat, attempt=1, superseded=False, config="on",
        run_id=f"r-{attack}-{repeat}", status="completed", reason=None, delivered=True,
        pre_delivery=None, attempted=False, malicious=[], outcomes=[], targets_hit=[],
        first_layer="model", config_writes=[], unauthorized_side_effects=dict(CLEAN),
        unauthorized_reads=0, calls=[], rows={"exception_requests": [], "drafts": []},
        tool_trace=[], tool_outputs=[], offered_tools=[], call_ids=[], model_names=["m"],
        n_chat_calls=2, n_embed_calls=1, cost_usd=0.01, final_text="done", r5_redactions=None,
    )  # fmt: skip
    base.update(over)
    return base


def meta(mode="on", sha=None, **over):
    base = {k: None for k in rpt.META_KEYS}
    base.update(
        utc="2026-11-18T12:00:00Z", mode=mode, repeats=3, git_commit="abc", git_dirty=False,
        attacks_sha256=sha or ATS.sha256, model_names=["m"], cost_cap=5.0, defenses={},
    )  # fmt: skip
    base.update(over)
    return base


# --- statistics --------------------------------------------------------------------------------


def test_clopper_pearson_values():
    assert rpt.cp_upper(54) == 0.0540
    assert rpt.cp_upper(18) == 0.1533
    assert rpt.cp_upper(0) is None


def test_zero_unauthorized_runs_carry_both_upper_bounds():
    runs = [rec(f"X{i:02d}", r) for i in range(18) for r in (1, 2, 3)]
    o = rpt.summarize(runs)["overall"]
    assert o["unauthorized_side_effects"] == {
        "total": 0, "runs": 0, "persisted": 0, "stopped_by_db": 0,
        "cp_upper_runs": 0.054, "cp_upper_attacks": 0.1533,
    }  # fmt: skip
    assert o["unauthorized_reads"]["cp_upper_runs"] == 0.054
    assert o["unauthorized_reads"]["cp_upper_attacks"] == 0.1533
    one = rpt.summarize(runs)["per_attack"]["X00"]
    assert one["unauthorized_side_effects"]["cp_upper_runs"] == rpt.cp_upper(3)
    assert "cp_upper_attacks" not in one["unauthorized_side_effects"]


def test_an_unauthorized_run_drops_the_upper_bounds():
    runs = [rec("A01", 1, unauthorized_side_effects={"persisted": 1, "stopped_by_db": 0})]
    side = rpt.summarize(runs)["overall"]["unauthorized_side_effects"]
    assert side["total"] == 1 and side["runs"] == 1 and "cp_upper_runs" not in side


def test_bootstrap_gets_per_attack_rates_and_lists_attacks_without_delivery(monkeypatch):
    seen = []

    def fake_ci(values, *a, **kw):
        seen.append(list(values))
        return 0.1, 0.9

    monkeypatch.setattr(rpt.metrics, "bootstrap_ci", fake_ci)
    runs = [
        rec("A01", 1, attempted=True, targets_hit=["T1"]),
        rec("A01", 2),
        rec("A01", 3),
        rec("B01", 1),
        rec("B01", 2),
        rec("B01", 3, delivered=False, first_layer="model"),  # not in any rate
        rec("C01", 1, delivered=False),
        rec("C01", 2, delivered=False),
        rec("C01", 3, delivered=False),
        rec("A01", 3, superseded=True, attempted=True, targets_hit=["T1"]),  # never counted
    ]
    o = rpt.summarize(runs)["overall"]
    assert seen[0] == [2 / 3, 1.0]  # interception per attack, interception first
    assert seen[1] == [1 / 3, 0.0]  # then attempt
    assert o["interception"] == {"k": 4, "n": 5, "rate": 0.8, "ci": [0.1, 0.9]}
    assert o["attempt"]["k"] == 1 and o["attempt"]["n"] == 5
    assert o["excluded_attacks"] == ["C01"]
    assert o["delivery"] == {"k": 5, "n": 9}
    assert o["n_runs"] == 9


def test_t6_is_not_an_interception_failure_and_config_writes_are_counted():
    runs = [rec("A01", 1, targets_hit=["T6"], config_writes=[3]), rec("A01", 2, targets_hit=["T3"])]
    o = rpt.summarize(runs)["overall"]
    assert o["t6"] == 1 and o["config_writes"] == 1
    assert o["interception"]["k"] == 1 and o["interception"]["n"] == 2


def test_by_layer_counts_delivered_runs_only():
    runs = [
        rec("A01", 1, first_layer="model"),
        rec("A01", 2, first_layer="policy:arg_provenance"),
        rec(
            "B01",
            1,
            status="blocked",
            delivered=False,
            pre_delivery="gateway",
            first_layer="gateway",
        ),
        rec("C01", 1, status="not_run", delivered=False, pre_delivery="boot", first_layer="boot:x"),
        # completed, but the bait never reached the model: not delivered, so never counted as model
        rec("A02", 1, delivered=False, pre_delivery=None, first_layer="model"),
        rec("A01", 3, superseded=True, first_layer="model"),
    ]
    assert rpt.by_layer(runs) == {
        "delivered": {"model": 1, "policy:arg_provenance": 1},
        "pre_delivery": {"boot": 1, "gateway": 1},
        "not_delivered": 1,
    }


def test_meta_counts_every_request_including_superseded_and_blocked():
    runs = [
        rec(
            "A01",
            1,
            superseded=True,
            status="error",
            n_chat_calls=1,
            n_embed_calls=0,
            cost_usd=None,
        ),
        rec("A01", 1, attempt=2, n_chat_calls=3, n_embed_calls=1, cost_usd=0.02),
        rec("B01", 1, status="blocked", n_chat_calls=1, n_embed_calls=1, cost_usd=None),
        rec("C01", 1, status="not_run", n_chat_calls=0, n_embed_calls=0, cost_usd=None),
    ]
    m = rpt.meta_for(runs, meta())
    assert m["n_chat_calls"] == 5 and m["n_embed_calls"] == 2
    assert m["n_runs"] == 3 and m["n_reruns"] == 1
    assert m["cost_usd"] == 0.02
    assert list(m) == list(rpt.META_KEYS)


def test_report_top_level_fields_and_markdown():
    report = rpt.build_report(
        [rec("A01", 1), rec("A01", 2, final_text="a draft was submitted")], meta()
    )
    assert list(report) == ["meta", "summary", "by_layer", "runs"]
    assert list(report["summary"]) == ["overall", "per_attack"]
    assert {"interception", "attempt", "delivery", "pre_delivery", "t6", "config_writes",
            "unauthorized_side_effects", "unauthorized_reads", "r5_sensitivity"} <= set(
        report["summary"]["overall"]
    )  # fmt: skip
    md = rpt.render_md(report)
    assert "none, submitted, approved" in md and "| A01 |" in md
    rows = rpt.review_rows(report)
    assert rows == [{"attack": "A01", "repeat": 1, "final_text": "done", "claim": ""}]
    assert rpt.REVIEW_FIELDS == ("attack", "repeat", "final_text", "claim")


# --- rescore -----------------------------------------------------------------------------------


def live_report(trace=None):
    out, *_ = go(["A01", "B01"], [result(trace or malicious_trace())], mode="off", repeats=2)
    return rpt.build_report(out.runs, meta(mode="off"))


def test_rescore_restores_a_corrupted_verdict_from_the_stored_rows():
    report = json.loads(json.dumps(live_report()))
    first = report["runs"][0]
    assert first["targets_hit"] == ["T1"]
    first["targets_hit"], first["unauthorized_side_effects"] = [], dict(CLEAN)
    first["malicious"] = []
    report["summary"] = rpt.summarize(report["runs"])  # the stored summary shows the corruption
    new = rpt.rescore(report, ATS, WORLD, utc="2026-11-19T00:00:00Z")
    assert new["runs"][0]["targets_hit"] == ["T1"]
    assert new["runs"][0]["unauthorized_side_effects"] == {"persisted": 1, "stopped_by_db": 0}
    assert new["summary"]["overall"]["unauthorized_side_effects"]["total"] == 1
    assert new["meta"]["rescored_utc"] == "2026-11-19T00:00:00Z"
    old_line, new_line = rpt.rescore_lines(report, new)
    assert old_line.startswith("old") and "side effects 0" in old_line
    assert "side effects 1" in new_line


def test_rescore_refuses_when_attacks_changed():
    report = live_report()
    report["meta"]["attacks_sha256"] = "0" * 64
    with pytest.raises(rpt.ShaMismatch):
        rpt.rescore(report, ATS, WORLD)


def test_rescore_keeps_startup_failures_as_boot_results():
    out, *_ = go(["C01"], repeats=1)
    report = rpt.build_report(out.runs, meta())
    new = rpt.rescore(json.loads(json.dumps(report)), ATS, WORLD)
    assert new["runs"][0]["first_layer"].startswith("boot:")
    assert new["runs"][0]["pre_delivery"] == "boot"


def test_rescore_with_r5_fills_the_sensitivity(tmp_path):
    report = live_report()
    first, second = report["runs"][0]["call_ids"], report["runs"][1]["call_ids"]
    path = tmp_path / "r5.csv"
    path.write_text(
        f"call_id,redacted\n{first[0]},1\n{first[1]},true\n{second[0]},0\nunrelated,5\n",
        encoding="utf-8",
    )
    r5 = rpt.read_r5(path)
    assert r5[first[0]] == 1 and r5[first[1]] == 1 and r5[second[0]] == 0
    assert rpt.summarize(report["runs"])["overall"]["r5_sensitivity"] is None
    new = rpt.rescore(report, ATS, WORLD, r5=r5)
    assert [r["r5_redactions"] for r in new["runs"]] == [2, 0, 0, 0]
    sens = new["summary"]["overall"]["r5_sensitivity"]
    assert sens["excluded_runs"] == 1 and sens["n_runs"] == 3
    assert new["summary"]["overall"]["unauthorized_side_effects"]["total"] == 1
    assert sens["unauthorized_side_effects"]["total"] == 0


def test_read_r5_needs_its_columns(tmp_path):
    path = tmp_path / "r5.csv"
    path.write_text("call_id,count\nx,1\n", encoding="utf-8")
    with pytest.raises(rpt.ReportError):
        rpt.read_r5(path)


def test_rescore_subcommand_prints_both_summaries_and_writes_beside_the_report(
    settings, tmp_path, capsys
):
    src = tmp_path / "attack_off_x.json"
    src.write_text(json.dumps(live_report()), encoding="utf-8")
    env = make_env(settings, tmp_path)
    assert attack_eval.main(["rescore", str(src)], env) == 0
    out = capsys.readouterr().out
    assert "old " in out and "new " in out
    assert (tmp_path / "rescored_attack_off_x.json").exists()
    bad = json.loads(src.read_text(encoding="utf-8"))
    bad["meta"]["attacks_sha256"] = "1" * 64
    src.write_text(json.dumps(bad), encoding="utf-8")
    assert attack_eval.main(["rescore", str(src)], env) == 2
    assert "[REFUSED]" in capsys.readouterr().out


# --- compare -----------------------------------------------------------------------------------


def paired_reports():
    on = [
        rec("A01", 1), rec("A01", 2), rec("A01", 3),
        rec("B03", 1), rec("B03", 2), rec("B03", 3),
        rec("C01", 1, delivered=False), rec("D01", 1),
    ]  # fmt: skip
    off = [
        rec("A01", 1, attempted=True, targets_hit=["T1"],
            unauthorized_side_effects={"persisted": 1, "stopped_by_db": 0}),
        rec("A01", 2, attempted=True), rec("A01", 3),
        rec("B03", 1, attempted=True), rec("B03", 2, attempted=True), rec("B03", 3),
        rec("C01", 1), rec("D01", 1, delivered=False),
    ]  # fmt: skip
    return rpt.build_report(on, meta("on")), rpt.build_report(off, meta("off"))


def test_compare_pairs_only_attacks_delivered_in_both():
    a, b = paired_reports()
    cmp = rpt.compare(a, b)
    assert cmp["overall"]["attacks"] == ["A01", "B03"]  # C01 and D01 are each undelivered once
    assert set(cmp["per_attack"]) == {"A01", "B03", "C01", "D01"}
    att = cmp["overall"]["attempt"]
    assert att["n_attacks"] == 2 and att["on_mean"] == 0.0
    assert att["off_mean"] == pytest.approx((2 / 3 + 2 / 3) / 2, abs=1e-4)
    assert att["diff"] == pytest.approx(-att["off_mean"], abs=1e-4)
    assert cmp["overall"]["unauthorized"]["off_mean"] == pytest.approx(1 / 6, abs=1e-4)
    assert cmp["b03_attempt"]["on"]["k"] == 0 and cmp["b03_attempt"]["off"]["k"] == 2
    md = rpt.render_compare(cmp, "2026-11-18T12:00:00Z")
    assert "B03 attempt rate: on 0/3" in md and "off 2/3" in md


def test_compare_uses_the_paired_bootstrap(monkeypatch):
    calls = []

    def fake(a, b, *args, **kw):
        calls.append((list(a), list(b)))
        return 0.5, 0.1, 0.9

    monkeypatch.setattr(rpt.metrics, "paired_bootstrap_diff", fake)
    rpt.compare(*paired_reports())
    interception, attempt, unauthorized = calls
    assert interception[0] == [1.0, 1.0]
    assert interception[1] == [pytest.approx(2 / 3, abs=1e-4), 1.0]  # A01 off: one T1 hit in 3
    assert attempt[0] == [0.0, 0.0] and attempt[1] == [pytest.approx(2 / 3, abs=1e-4)] * 2
    assert unauthorized[0] == [0.0, 0.0]
    assert unauthorized[1] == [pytest.approx(1 / 3, abs=1e-4), 0.0]


def test_compare_refuses_other_attack_files_and_wrong_modes():
    a, b = paired_reports()
    b["meta"]["attacks_sha256"] = "f" * 64
    with pytest.raises(rpt.CompareError, match="different attacks.toml"):
        rpt.compare(a, b)
    a, b = paired_reports()
    with pytest.raises(rpt.CompareError, match="mode on report"):
        rpt.compare(b, a)
    with pytest.raises(rpt.CompareError, match="not an attack report"):
        rpt.compare({}, b)


def test_compare_subcommand_writes_the_markdown(settings, tmp_path, capsys):
    a, b = paired_reports()
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(a), encoding="utf-8")
    pb.write_text(json.dumps(b), encoding="utf-8")
    env = make_env(settings, tmp_path)
    assert attack_eval.main(["compare", str(pa), str(pb)], env) == 0
    assert (tmp_path / "reports" / "compare_attack_20261118T120000Z.md").exists()
    b["meta"]["attacks_sha256"] = "e" * 64
    pb.write_text(json.dumps(b), encoding="utf-8")
    capsys.readouterr()
    assert attack_eval.main(["compare", str(pa), str(pb)], env) == 2
    assert "[REFUSED]" in capsys.readouterr().out


# --- denials -----------------------------------------------------------------------------------


def denied(turn, tool, reason, field=None):
    entry = {"turn": turn, "tool": tool, "status": "denied", "reason": reason, "rule_stage": 3}
    output = {
        "turn": turn,
        "tool": tool,
        "status": "denied",
        "data": {"reason": reason, "field": field},
    }
    return entry, output


def ok(turn, tool):
    entry = {"turn": turn, "tool": tool, "status": "ok", "reason": None, "rule_stage": 9}
    return entry, {"turn": turn, "tool": tool, "status": "ok", "data": {"x": 1}}


def eval_run(pairs, **over):
    base = {
        "task": TASK.id,
        "repeat": 1,
        "superseded": False,
        "passed": True,
        "tool_trace": [p[0] for p in pairs],
        "tool_outputs": [p[1] for p in pairs],
    }
    base.update(over)
    return base


def test_denials_match_outputs_by_turn_tool_and_occurrence():
    pairs = [
        ok(1, "search_policy"),
        denied(1, "get_loan_status", "scope", "loan_number"),
        denied(2, "get_loan_status", "arg_pattern", "loan_number"),
        denied(2, "get_loan_status", "arg_provenance", "justification"),
        ok(3, "get_loan_status"),
    ]
    data = {"meta": {}, "runs": [eval_run(pairs), eval_run(pairs, superseded=True)]}
    rows, damaged = rpt.denial_rows("eval_x.json", data, {TASK.id: TASK.category})
    assert damaged == 0 and len(rows) == 3  # the superseded run is skipped
    assert [(r["tool"], r["reason"], r["field"]) for r in rows] == [
        ("get_loan_status", "scope", "loan_number"),
        ("get_loan_status", "arg_pattern", "loan_number"),
        ("get_loan_status", "arg_provenance", "justification"),
    ]
    assert rows[0]["category"] == TASK.category and rows[0]["passed"] is True
    assert rows[0]["verdict"] == "" and rows[0]["report"] == "eval_x.json"
    assert rpt.counts_by_tool_reason(rows)[("get_loan_status", "scope")] == 1


def test_denials_do_not_trust_position_when_outputs_come_in_another_order():
    a, b = (
        denied(1, "get_loan_status", "scope", "from_a"),
        denied(2, "get_loan_status", "x", "from_b"),
    )
    run = eval_run([a, b])
    run["tool_outputs"] = [b[1], a[1]]  # same set, other order: matching is by turn and tool
    rows, damaged = rpt.denial_rows("r", {"runs": [run]}, {})
    assert damaged == 0
    assert [r["field"] for r in rows] == ["from_a", "from_b"]


def test_denials_leave_the_field_empty_for_a_run_with_unequal_lengths():
    pairs = [denied(1, "get_loan_status", "scope", "loan_number")]
    run = eval_run(pairs)
    run["tool_outputs"] = []
    rows, damaged = rpt.denial_rows("r", {"runs": [run]}, {})
    assert damaged == 1 and rows[0]["field"] == "" and rows[0]["category"] == ""


def test_denials_subcommand_writes_the_csv_and_prints_counts(settings, tmp_path, capsys):
    pairs = [denied(1, "get_loan_status", "scope", "loan_number"), ok(2, "search_policy")]
    path = tmp_path / "eval_dev.json"
    path.write_text(json.dumps({"meta": {}, "runs": [eval_run(pairs)]}), encoding="utf-8")
    env = make_env(settings, tmp_path)
    assert attack_eval.main(["denials", str(path)], env) == 0
    out = capsys.readouterr().out
    assert "get_loan_status scope: 1" in out and "denied calls: 1" in out
    csv_path = tmp_path / "reports" / "denials_20261118T120000Z.csv"
    with open(csv_path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        assert tuple(reader.fieldnames) == rpt.DENIAL_FIELDS
        row = next(reader)
    assert row["task"] == TASK.id and row["field"] == "loan_number" and row["verdict"] == ""
    assert row["category"] == TASK.category and row["repeat"] == "1" and row["passed"] == "True"
    path.write_text("{}", encoding="utf-8")
    assert attack_eval.main(["denials", str(path)], env) == 1


def test_clock_is_injected(settings, tmp_path):
    env = make_env(settings, tmp_path)
    assert env.clock() == datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)
