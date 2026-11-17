"""The agent-eval runner and subcommands with fakes: no network, no database, files in tmp_path."""

import csv
import itertools
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import agent_eval as ae
from agent_fakes import FakeRepo, agent_config
from rag_agent.agent import mockdata
from rag_agent.agent.boot import AgentConfig
from rag_agent.agent.loop import AgentResult
from rag_agent.agent.policy_config import load_policy
from rag_agent.agent_eval import report as rpt
from rag_agent.agent_eval.checks import World
from rag_agent.agent_eval.tasks import Task, load_tasks

TS = load_tasks()
WORLD = World.build(
    load_policy(env={}), mockdata.load_users(), mockdata.load_loans(), mockdata.load_contacts()
)
FIXED = datetime(2026, 11, 17, 12, 0, 0, tzinfo=UTC)
AUDIT_OK = {"unauthorized_side_effects": 0}
TABLES_OK = (
    {t: 0 for t in ("loans", "contacts", "exception_requests", "drafts", "policy_events")},
    {"raised_needed": True, "decision_needs_human": True, "reason_needed": True},
)
_ids = itertools.count(1)


def result(status="completed", reason=None, cost=0.001, n_chat=1, text="Done."):
    return asdict(
        AgentResult(
            run_id=f"run{next(_ids):06d}",
            status=status,
            reason=reason,
            final_text=text,
            n_chat_calls=n_chat,
            cost_usd=cost,
            model_names=["model-a"],
        )
    )


def gw(reason, status="error"):
    return result(status=status, reason=reason, cost=None)


def task(task_id="X01"):
    return Task(
        id=task_id,
        split="dev",
        category="single",
        user="uw-01",
        question="What is the TDS limit?",
        status=("completed",),
    )


class Fake:
    """Deps whose run_fn plays a script of results (then completed results forever)."""

    def __init__(self, script=(), audit=AUDIT_OK):
        self.script = list(script)
        self.sleeps: list[float] = []
        self.calls: list[str] = []
        self.deps = ae.Deps(
            run_fn=self.run,
            repo=FakeRepo(),
            audit_fn=lambda: dict(audit),
            reset_fn=lambda: self.calls.append("reset"),
            drafts_fn=lambda run_id: [],
            setup_fn=lambda t: self.calls.append("setup"),
            sleep=self.sleeps.append,
            clock=lambda: FIXED,
        )

    def run(self, question, user):
        self.calls.append("run")
        return self.script.pop(0) if self.script else result()


def run(fake, tasks=None, repeats=1, pace=0.0, cost_cap=1.0, out=None):
    lines: list[str] = []
    outcome = ae.run_tasks(
        tasks or [task()],
        TS.loans,
        WORLD,
        repeats=repeats,
        pace=pace,
        cost_cap=cost_cap,
        deps=fake.deps,
        out=lines.append,
    )
    return outcome, lines


# --- the runner core -----------------------------------------------------------------------------


def test_rate_limited_then_success_reruns_once_after_65_s():
    fake = Fake([gw("rate_limited"), result()])
    outcome, _ = run(fake)
    assert outcome.aborted is None
    assert fake.sleeps == [65.0]
    assert fake.calls == ["reset", "setup", "run", "reset", "setup", "run"]
    first, second = outcome.runs
    assert first["superseded"] is True and first["passed"] is None and first["attempt"] == 1
    assert {c["kind"] for c in first["checks"]} == {"policy"}
    assert second["superseded"] is False and second["passed"] is True and second["attempt"] == 2


def test_timeout_then_success_waits_10_s():
    fake = Fake([gw("timeout"), result()])
    outcome, _ = run(fake)
    assert outcome.aborted is None and fake.sleeps == [10.0]


def test_second_gateway_error_aborts():
    fake = Fake([gw("rate_limited"), gw("gateway_unavailable")])
    outcome, _ = run(fake)
    assert "second gateway error" in outcome.aborted
    assert len(outcome.runs) == 2


@pytest.mark.parametrize(
    "status,reason",
    [
        ("blocked", "blocked_policy"),
        ("error", "budget_exceeded"),
        ("error", "auth_config"),
        ("error", "bad_request"),
    ],
)
def test_fatal_gateway_errors_abort_without_rerun(status, reason):
    fake = Fake([gw(reason, status)])
    outcome, _ = run(fake, tasks=[task("X01"), task("X02")])
    assert outcome.aborted == f"gateway error {reason} in X01 r1"
    assert fake.calls.count("run") == 1 and fake.sleeps == []


def test_failed_policy_check_aborts_and_prints_the_run():
    fake = Fake(audit={"unauthorized_side_effects": 1})
    outcome, lines = run(fake, tasks=[task("X01"), task("X02")])
    assert outcome.aborted.startswith("policy check failed")
    assert fake.calls.count("run") == 1
    fail = [x for x in lines if x.startswith("[FAIL] policy")]
    assert fail and "X01 r1 run" in fail[0] and "P1 unauthorized side effects == 0" in fail[0]


def test_policy_failure_in_a_superseded_attempt_also_aborts():
    fake = Fake([gw("rate_limited")], audit={"unauthorized_side_effects": 1})
    outcome, _ = run(fake)
    assert outcome.aborted.startswith("policy check failed") and fake.sleeps == []


def test_cost_cap_aborts():
    fake = Fake([result(cost=0.6), result(cost=0.6)])
    outcome, _ = run(fake, tasks=[task("X01"), task("X02"), task("X03")])
    assert outcome.aborted.startswith("cost cap exceeded")
    assert fake.calls.count("run") == 2


def test_missing_cost_header_aborts():
    fake = Fake([result(cost=None, n_chat=2)])
    outcome, _ = run(fake)
    assert outcome.aborted == "cost header missing in X01 r1"


def test_no_chat_call_needs_no_cost_header():
    fake = Fake([result(status="rejected_input", reason="too_long", cost=None, n_chat=0)])
    outcome, _ = run(fake)
    assert outcome.aborted is None


def test_pace_sleeps_between_runs_only():
    fake = Fake()
    outcome, _ = run(fake, tasks=[task("X01"), task("X02")], repeats=2, pace=0.5)
    assert outcome.aborted is None and fake.sleeps == [0.5, 0.5, 0.5]


def test_unexpected_error_aborts_with_type_name():
    fake = Fake()

    def boom(t):
        raise RuntimeError("db down")

    fake.deps.setup_fn = boom
    outcome, _ = run(fake)
    assert outcome.aborted == "unexpected error (RuntimeError)"


# --- run: preflight and outputs ------------------------------------------------------------------


@pytest.fixture
def env_for(settings, tmp_path):
    def make(fake=None, *, dirty=False, config=None, reranker=None, **settings_kw):
        s = settings.model_copy(update={"agent_log_dir": str(tmp_path / "agent"), **settings_kw})
        made = []

        def make_deps(*args):
            made.append(args)
            return (fake or Fake()).deps

        env = ae.Env(
            load_settings=lambda: s,
            startup=lambda st: (
                config or agent_config(),
                SimpleNamespace(settings=st, reranker_check=lambda _s: reranker),
            ),
            git_info=lambda: ("abc123", dirty),
            dsn=lambda: "postgresql://fake/db",
            table_check=lambda dsn: TABLES_OK,
            make_deps=make_deps,
            log_dir=lambda: str(tmp_path / "agent"),
            ledger_path=tmp_path / "eval" / "test_runs.csv",
            sleep=lambda _x: None,
            clock=lambda: FIXED,
        )
        env.made = made  # type: ignore[attr-defined]
        return env

    return make


def test_test_split_without_final_is_refused(env_for, capsys):
    env = env_for()
    assert ae.main(["run", "--split", "test", "--repeats", "1"], env) == 2
    assert "[REFUSED] the test split needs --final" in capsys.readouterr().out
    assert env.made == []


def test_test_split_with_dirty_tree_is_refused(env_for, capsys):
    env = env_for(dirty=True)
    assert ae.main(["run", "--split", "test", "--repeats", "1", "--final"], env) == 2
    assert "clean git tree" in capsys.readouterr().out
    assert env.made == []


def test_a_defense_off_fails_before_any_run(env_for, capsys):
    cfg = agent_config()
    pol = replace(cfg.policy, defenses={**cfg.policy.defenses, "scope_check": False})
    env = env_for(config=AgentConfig(cfg.facts, pol, cfg.specs))
    assert ae.main(["run", "--only", "D01"], env) == 1
    assert capsys.readouterr().out.strip() == "[FAIL] defenses off: scope_check"
    assert env.made == []


@pytest.mark.parametrize(
    "kw,expected",
    [
        ({"llm_mode": "direct"}, "gateway mode only"),
        ({"reranker": "reranker_unavailable"}, "reranker: reranker_unavailable"),
    ],
)
def test_preflight_failures_exit_1(env_for, capsys, kw, expected):
    env = env_for(**kw)
    assert ae.main(["run", "--only", "D01"], env) == 1
    assert expected in capsys.readouterr().out
    assert env.made == []


def test_missing_tables_fail(env_for, capsys):
    env = env_for()
    env.table_check = lambda dsn: ({**TABLES_OK[0], "drafts": None}, TABLES_OK[1])
    assert ae.main(["run", "--only", "D01"], env) == 1
    assert "missing: drafts" in capsys.readouterr().out


def test_only_is_refused_on_test_and_unknown_ids_fail(env_for):
    env = env_for()
    assert ae.main(["run", "--split", "test", "--final", "--only", "T01"], env) == 2
    assert ae.main(["run", "--only", "T01"], env) == 1  # a test id is not a dev task
    assert env.made == []


def test_model_alias_replaces_chat_model_for_the_run(env_for, tmp_path):
    env = env_for()
    assert ae.main(["run", "--only", "D01", "--repeats", "1", "--model", "chat-b"], env) == 0
    report = json.loads(next((tmp_path / "agent").glob("eval_dev_chat-b_*.json")).read_text())
    assert report["meta"]["model_alias"] == "chat-b"


def test_dev_run_writes_report_files_and_no_ledger(env_for, tmp_path):
    env = env_for()
    assert ae.main(["run", "--only", "D01,D02", "--repeats", "2"], env) == 0
    out = tmp_path / "agent"
    stem = "dev_chat-cloud_20261117T120000Z"
    for name in (f"eval_{stem}.json", f"eval_{stem}.md", f"review_{stem}.csv"):
        assert (out / name).exists()
    assert (out / f"failures_{stem}.csv").exists()
    report = json.loads((out / f"eval_{stem}.json").read_text())
    assert tuple(report) == rpt.TOP_KEYS and tuple(report["meta"]) == rpt.META_KEYS
    assert report["meta"]["n_runs"] == 4 and report["meta"]["git_commit"] == "abc123"
    assert report["meta"]["tasks_sha256"] == TS.sha256
    assert all(r["superseded"] is False for r in report["runs"])
    assert not (tmp_path / "eval" / "test_runs.csv").exists()


def test_abort_writes_only_the_aborted_file(env_for, tmp_path):
    env = env_for(Fake([gw("budget_exceeded")]))
    assert ae.main(["run", "--only", "D01"], env) == 1
    files = sorted(p.name for p in (tmp_path / "agent").iterdir())
    assert files == ["aborted_20261117T120000Z.json"]
    data = json.loads((tmp_path / "agent" / files[0]).read_text())
    assert data["aborted"].startswith("gateway error budget_exceeded")
    assert tuple(data["meta"]) == rpt.META_KEYS and len(data["runs"]) == 1


def test_ledger_header_created_once_and_lines_appended(env_for, tmp_path):
    for _ in range(2):
        env = env_for()
        assert ae.main(["run", "--split", "test", "--final", "--repeats", "1"], env) == 0
    rows = list(csv.reader((tmp_path / "eval" / "test_runs.csv").open()))
    assert rows[0] == list(rpt.LEDGER_HEADER)
    assert len(rows) == 3 and rows[1] == rows[2]
    values = dict(zip(rows[0], rows[1], strict=True))
    assert values["n_runs"] == "25" and values["n_tasks"] == "25"


# --- rescore, compare, tally, prescan ------------------------------------------------------------


@pytest.fixture
def dev_report(env_for, tmp_path):
    assert ae.main(["run", "--only", "D01,D02,D03", "--repeats", "2"], env_for()) == 0
    return next((tmp_path / "agent").glob("eval_dev_*.json"))


LATER = datetime(2026, 11, 18, 9, 30, 0, tzinfo=UTC)


def test_rescore_reproduces_the_stored_totals(dev_report, env_for):
    env = env_for()
    env.clock = lambda: LATER
    assert ae.main(["rescore", str(dev_report)], env) == 0
    old = json.loads(dev_report.read_text())
    new = json.loads(dev_report.with_name(f"rescored_{dev_report.stem}.json").read_text())
    assert rpt.totals(new) == rpt.totals(old)
    assert new["behaviour"] == old["behaviour"]
    assert [r["checks"] for r in new["runs"]] == [r["checks"] for r in old["runs"]]
    # the stored meta is kept; the rescore adds the task file it used and when
    assert {k: new["meta"][k] for k in rpt.META_KEYS} == old["meta"]
    assert tuple(new["meta"]) == (*rpt.META_KEYS, *rpt.RESCORE_META_KEYS)
    assert new["meta"]["rescored_tasks_sha256"] == TS.sha256
    assert new["meta"]["rescored_utc"] == "2026-11-18T09:30:00Z"
    assert dev_report.with_name(f"rescored_{dev_report.stem}.md").exists()


def test_rescore_refuses_another_task_file_unless_allowed(dev_report, tmp_path):
    data = json.loads(dev_report.read_text())
    data["meta"]["tasks_sha256"] = "0" * 64
    other = tmp_path / "other.json"
    other.write_text(json.dumps(data))
    assert ae.main(["rescore", str(other)]) == 2
    assert ae.main(["rescore", str(other), "--allow-new-tasks"]) == 0
    new = json.loads((tmp_path / "rescored_other.json").read_text())
    assert new["meta"]["tasks_sha256"] == "0" * 64  # stored value kept
    assert new["meta"]["rescored_tasks_sha256"] == TS.sha256


def test_compare_refuses_different_tasks_sha256(dev_report, env_for, tmp_path, capsys):
    data = json.loads(dev_report.read_text())
    data["meta"]["tasks_sha256"] = "0" * 64
    other = tmp_path / "other.json"
    other.write_text(json.dumps(data))
    env = env_for()
    assert ae.main(["compare", str(dev_report), str(other)], env) == 2
    assert "[REFUSED] tasks_sha256 differs" in capsys.readouterr().out
    data["meta"]["tasks_sha256"] = TS.sha256
    data["meta"]["model_alias"] = "chat-b"
    other.write_text(json.dumps(data))
    assert ae.main(["compare", str(dev_report), str(other)], env) == 0
    assert (tmp_path / "agent" / "compare_20261117T120000Z.md").exists()


def _csv(path, fields, rows):
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def test_tally_rejects_an_empty_verdict(tmp_path, capsys):
    path = tmp_path / "review.csv"
    rows = [
        {"task": "D01", "repeat": 1, "unit": "u", "verdict": "supported", "note": ""},
        {"task": "D02", "repeat": 1, "unit": "u", "verdict": "", "note": ""},
    ]
    _csv(path, rpt.REVIEW_FIELDS, rows)
    assert ae.main(["tally", str(path)]) == 1
    assert "line 3: empty verdict" in capsys.readouterr().out
    rows[1]["verdict"] = "partial"
    _csv(path, rpt.REVIEW_FIELDS, rows)
    assert ae.main(["tally", str(path)]) == 0
    assert "tasks: 2" in capsys.readouterr().out


def test_tally_failures_by_cause(tmp_path, capsys):
    path = tmp_path / "failures.csv"
    row = {"task": "D01", "repeat": 1, "run_id": "r", "failed_checks": "has", "note": ""}
    _csv(path, rpt.FAILURE_FIELDS, [{**row, "cause": "checker"}])
    assert ae.main(["tally", str(path)]) == 0
    assert "checker: 1" in capsys.readouterr().out
    _csv(path, rpt.FAILURE_FIELDS, [{**row, "cause": "weather"}])
    assert ae.main(["tally", str(path)]) == 1


class CountingClient:
    def __init__(self, fail_at=None):
        self.sent, self.fail_at = [], fail_at

    def chat(self, messages, **kw):
        if self.fail_at is not None and len(self.sent) == self.fail_at:
            raise RuntimeError("gateway said no")
        assert kw == {"max_tokens": 16} and messages[0]["role"] == "user"
        self.sent.append(messages[0]["content"])


def test_prescan_sends_every_question_once_and_prints_no_text(env_for, capsys):
    client, sleeps = CountingClient(), []
    env = env_for()
    env.client_factory, env.sleep = (lambda s: client), sleeps.append
    assert ae.main(["prescan"], env) == 0
    out = capsys.readouterr().out
    assert out == f"requests sent: {len(TS.tasks)}\n" and len(TS.tasks) == 36
    assert len(client.sent) == 36 and sleeps == [0.2] * 35
    assert all("{" not in q for q in client.sent)  # placeholders resolved


def test_prescan_stops_on_first_error_and_needs_gateway_mode(env_for, capsys):
    env = env_for()
    env.client_factory = lambda s: CountingClient(fail_at=3)
    assert ae.main(["prescan"], env) == 1
    assert "failed after 3 requests: RuntimeError" in capsys.readouterr().out
    assert ae.main(["prescan"], env_for(llm_mode="direct")) == 1
