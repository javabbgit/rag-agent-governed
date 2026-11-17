"""Report aggregation, sampling, ledger rows, tally and compare on synthetic run records.
Pure: no network, no database, no files."""

from dataclasses import asdict

import pytest

from rag_agent.agent import mockdata
from rag_agent.agent.loop import AgentResult
from rag_agent.agent_eval import report as rpt
from rag_agent.agent_eval.checks import BEHAVIOUR, POLICY, Check
from rag_agent.agent_eval.tasks import Task

USERS = mockdata.load_users()
BASE = {
    "utc": "2026-11-17T12:00:00Z",
    "split": "dev",
    "model_alias": "chat-cloud",
    "git_commit": "abc123",
    "git_dirty": False,
    "tasks_sha256": "t" * 64,
    "prompt_sha256": "p" * 64,
    "tools_lock_sha256": "l" * 64,
    "policy_sha256": "y" * 64,
    "repeats": 2,
    "llm_timeout_s": 75.0,
    "llm_max_retries": 0,
}


def task(task_id, category="single", user="uw-01", **kw):
    return Task(id=task_id, split="dev", category=category, user=user, question="q", **kw)


def record(t, repeat, passed, *, superseded=False, attempt=1, text="Done.", **res_kw):
    res = asdict(
        AgentResult(
            run_id=f"{t.id}-{repeat}-{attempt}",
            status="completed",
            final_text=text,
            n_chat_calls=2,
            n_tool_calls=1,
            n_embed_calls=1,
            cost_usd=0.01,
            latency_ms_total=100.0 * repeat,
            model_names=["model-a"],
        )
    )
    res.update(res_kw)
    run_checks = [Check(BEHAVIOUR, "status in ['completed']", passed), Check(POLICY, "P1", True)]
    return rpt.run_record(
        t,
        repeat,
        attempt,
        superseded,
        "q",
        res,
        [],
        [],
        {},
        run_checks,
        None if superseded else passed,
    )


A, B, C = task("A01"), task("B01", user="br-01"), task("C01", category="multi")
TASKS = {t.id: t for t in (A, B, C)}


def synthetic():
    return [
        record(A, 1, False, superseded=True, status="error", reason="rate_limited", cost_usd=None),
        record(A, 1, True, attempt=2),
        record(A, 2, True),
        record(B, 1, True),
        record(B, 2, False),
        record(C, 1, False),
        record(C, 2, False),
    ]


def test_aggregation_pass_at_1_pass_all_unstable_by_category_and_role():
    rep = rpt.summarize(synthetic(), TASKS, USERS, BASE)
    assert tuple(rep) == rpt.TOP_KEYS
    assert tuple(rep["meta"]) == rpt.META_KEYS
    b = rep["behaviour"]
    assert set(b) == {"pass_at_1", "pass_all", "unstable", "by_category", "by_role"}
    assert b["pass_at_1"]["value"] == 0.5
    assert (b["pass_at_1"]["runs_passed"], b["pass_at_1"]["n_runs"]) == (3, 6)
    assert b["pass_at_1"]["ci_lo"] <= 0.5 <= b["pass_at_1"]["ci_hi"]
    assert b["pass_all"] == {"value": 0.3333, "tasks_all_pass": 1, "n_tasks": 3}
    assert b["unstable"] == ["B01"]
    single, multi = b["by_category"]["single"], b["by_category"]["multi"]
    assert (single["runs_passed"], single["n_runs"], single["tasks_all_pass"]) == (3, 4, 1)
    assert (multi["pass_at_1"], multi["pass_all"]) == (0.0, 0.0)
    assert b["by_role"]["underwriter"]["runs_passed"] == 2
    assert b["by_role"]["broker"]["n_runs"] == 2
    assert rep["policy"] == {"passed": 6, "total": 6}


def test_meta_counts_cover_every_attempt_but_n_runs_only_scored():
    rep = rpt.summarize(synthetic(), TASKS, USERS, BASE)
    m = rep["meta"]
    assert m["n_runs"] == 6 and m["n_reruns"] == 1
    assert m["n_chat_calls"] == 14  # 7 attempts x 2, superseded included
    assert m["n_embed_calls"] == 7
    assert m["cost_usd"] == 0.06  # the superseded attempt had no cost
    assert m["model_names"] == ["model-a"]
    assert rep["efficiency"]["cost_usd"]["mean_per_task"] == 0.02
    assert rep["efficiency"]["latency_ms_total"]["p50"] == 100.0
    assert len(rep["runs"]) == 7


def test_citation_and_numbers():
    gold = task("G01", gold_docs=("POL-003",), cite=True)
    runs = [
        record(
            gold,
            1,
            True,
            sources=[["S1", "POL-003#ratios"]],
            cited_markers=["S1"],
            text="The maximum GDS ratio is 39 percent [S1].",
        ),
        record(gold, 2, True, unknown_markers=["S9"], text="Nothing to cite here at all."),
    ]
    runs[0]["checks"].append({"kind": BEHAVIOUR, "name": rpt.CITE_CHECK, "passed": True})
    rep = rpt.summarize(runs, {"G01": gold}, USERS, BASE)
    c = rep["citation"]
    assert c["no_unknown"] == {"runs": 1, "of": 2}
    assert c["cite_checks"] == {"passed": 1, "total": 1}
    assert c["gold_doc_hits"] == {"hits": 1, "of": 1}
    assert rep["numbers"]["ungrounded_units"] == 1
    assert rep["numbers"]["runs"][0]["task"] == "G01"


def test_review_sampling_ten_runs_plus_awaiting_first_repeat_only():
    tasks = [task(f"X{i:02d}") for i in range(1, 15)]
    tasks[13] = task("X14", category="awaiting")
    tasks[1] = task("X02", category="awaiting")  # also picked by the sample: no duplicate
    by_id = {t.id: t for t in tasks}
    text = "The first sentence has enough words. The second sentence has enough words too."
    runs = [record(t, r, True, text=text) for t in tasks for r in (1, 2)]
    rep = {"runs": runs}
    picked = [r["task"] for r in rpt.review_runs(rep, by_id)]
    assert picked == ["X01", "X02", "X03", "X05", "X06", "X08", "X09", "X10", "X12", "X13", "X14"]
    rows = rpt.review_rows(rep, by_id)
    assert {r["repeat"] for r in rows} == {1}
    assert len(rows) == 2 * len(picked)
    assert tuple(rows[0]) == rpt.REVIEW_FIELDS and rows[0]["verdict"] == ""


def test_failure_rows_and_ledger_row():
    rep = rpt.summarize(synthetic(), TASKS, USERS, BASE)
    rows = rpt.failure_rows(rep)
    assert [(r["task"], r["repeat"]) for r in rows] == [("B01", 2), ("C01", 1), ("C01", 2)]
    assert rows[0]["failed_checks"] == "status in ['completed']"
    assert tuple(rows[0]) == rpt.FAILURE_FIELDS
    line = rpt.ledger_row(rep)
    assert len(line) == len(rpt.LEDGER_HEADER)
    values = dict(zip(rpt.LEDGER_HEADER, line, strict=True))
    assert values["n_runs"] == "6" and values["n_reruns"] == "1"
    assert values["pass_at_1"] == "0.5000" and values["tasks_all_pass"] == "1"
    assert values["model_name"] == "model-a"


def test_markdown_has_summary_and_a_section_per_failed_run():
    md = rpt.render_markdown(rpt.summarize(synthetic(), TASKS, USERS, BASE))
    assert "policy checks | 6/6" in md
    assert "## B01 r2 (B01-2-1)" in md and "## C01 r1" in md
    assert "## A01" not in md


def test_tally_counts_and_rejects_empty_or_unknown_values():
    ok = [
        {"task": "A01", "verdict": "supported"},
        {"task": "A01", "verdict": "partial"},
        {"task": "B01", "verdict": "unsupported"},
    ]
    assert rpt.tally_review(ok) == {"supported": 1, "partial": 1, "unsupported": 1, "tasks": 2}
    with pytest.raises(rpt.TallyError, match="line 3: empty verdict"):
        rpt.tally_review([ok[0], {"task": "B01", "verdict": " "}])
    with pytest.raises(rpt.TallyError, match="not one of"):
        rpt.tally_review([{"task": "A01", "verdict": "maybe"}])
    assert rpt.tally_failures([{"cause": "model"}, {"cause": "task"}]) == {
        "model": 1,
        "checker": 0,
        "task": 1,
    }
    with pytest.raises(rpt.TallyError):
        rpt.tally_failures([{"cause": ""}])


def test_compare_refuses_different_tasks_sha_and_diffs_per_task():
    a = rpt.summarize(synthetic(), TASKS, USERS, BASE)
    b = rpt.summarize(synthetic(), TASKS, USERS, {**BASE, "model_alias": "chat-local"})
    cmp = rpt.compare(a, b)
    assert cmp["diff"]["mean"] == 0.0 and cmp["n_tasks"] == 3
    assert "chat-local" in rpt.render_compare(cmp, "2026-11-17T12:00:00Z")
    for key, value in (("tasks_sha256", "x" * 64), ("split", "test"), ("repeats", 3)):
        other = rpt.summarize(synthetic(), TASKS, USERS, {**BASE, key: value})
        with pytest.raises(rpt.CompareError, match=key):
            rpt.compare(a, other)
