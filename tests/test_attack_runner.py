"""runner.py and the run subcommand of scripts/attack_eval.py with fakes. No network or DB."""

import csv
import itertools
import json
import os
from datetime import UTC, datetime

import pytest

import attack_eval
from rag_agent.agent_attack import report as rpt
from rag_agent.agent_attack import runner
from rag_agent.agent_attack.attacks import ATTACKS_PATH, load_attacks
from rag_agent.agent_attack.harness import world
from test_attack_matrix import fake_deps

ATS = load_attacks()
WORLD = world()
TEXT = ATTACKS_PATH.read_text(encoding="utf-8")
L_MAJOR = ATS.loans["L_major"]
PREP = "prepare_exception_request"
_IDS = itertools.count(1)


def result(trace=(), status="completed", reason=None, cost=0.01, chat=2, embed=0, **over):
    n = next(_IDS)
    base = {
        "run_id": f"run{n:05d}",
        "status": status,
        "reason": reason,
        "final_text": "done",
        "message": "",
        "exception_request_ids": [],
        "draft_ids": [],
        "tool_trace": list(trace),
        "tool_outputs": [],
        "sources": [],
        "n_chat_calls": chat,
        "n_tool_calls": len(trace),
        "n_denials": 0,
        "n_embed_calls": embed,
        "call_ids": [f"call{n}a", f"call{n}b"],
        "model_names": ["fake-model"],
        "cost_usd": cost,
    }
    base.update(over)
    return base


def malicious_trace():
    """A01's signature call that ended in a draft: a T1 hit, one unauthorized side effect."""
    return [
        {
            "turn": 2,
            "rule_stage": 8,
            "tool": PREP,
            "status": "awaiting_raise",
            "reason": None,
            "reached_executor": True,
            "args_digest": {"loan_number": L_MAJOR, "exception_type": "gds_tds_ratio"},
        }
    ]


class FakeRun:
    """run_fn: pops one scripted result (or callable) per call; clean results afterwards."""

    def __init__(self, script=()):
        self.script = list(script)
        self.calls = []

    def __call__(self, question, user, **kw):
        self.calls.append((question, user, kw))
        if not self.script:
            return result()
        item = self.script.pop(0)
        return item() if callable(item) else item


def run_deps(script=()):
    fr = FakeRun(script)
    sleeps = []
    return runner.RunDeps(fake_deps(), fr, lambda a: None, sleeps.append), fr, sleeps


def go(attack_ids, script=(), *, mode="on", repeats=1, cost_cap=10.0):
    deps, fr, sleeps = run_deps(script)
    attacks = [ATS.by_id(i) for i in attack_ids]
    lines = []
    out = runner.run_attacks(
        attacks,
        ATS,
        WORLD,
        mode=mode,
        repeats=repeats,
        cost_cap=cost_cap,
        deps=deps,
        out=lines.append,
    )
    return out, fr, sleeps, lines


# --- the run loop ------------------------------------------------------------------------------


def test_clean_runs_are_recorded_in_order_and_matrix_only_is_skipped():
    out, fr, _, _ = go(["A01", "C04", "B01"], repeats=2)
    assert out.aborted is None
    assert [(r["attack"], r["repeat"]) for r in out.runs] == [
        ("A01", 1),
        ("A01", 2),
        ("B01", 1),
        ("B01", 2),
    ]
    assert len(fr.calls) == 4
    assert out.cost_usd == pytest.approx(0.04)


def test_mode_on_aborts_on_the_first_unauthorized_effect():
    out, fr, _, lines = go(["A01", "B01"], [result(malicious_trace())], repeats=2)
    assert out.aborted is not None and "unauthorized effect in mode on" in out.aborted
    assert len(out.runs) == 1 and len(fr.calls) == 1
    run = out.runs[0]
    assert run["targets_hit"] == ["T1"]
    assert run["unauthorized_side_effects"] == {"persisted": 1, "stopped_by_db": 0}
    assert any("[FAIL] unauthorized effect" in line for line in lines)


def test_mode_off_goes_on_after_an_unauthorized_effect():
    out, fr, _, _ = go(["A01"], [result(malicious_trace())], mode="off", repeats=2)
    assert out.aborted is None
    assert len(out.runs) == 2 and len(fr.calls) == 2
    assert out.runs[0]["config"] == "all_off"
    assert out.runs[0]["unauthorized_side_effects"]["persisted"] == 1


def test_rate_limited_error_waits_and_reruns_once():
    err = result(status="error", reason="rate_limited", cost=None, chat=1)
    out, fr, sleeps, _ = go(["B01"], [err, result()])
    assert out.aborted is None
    assert sleeps == [65.0]
    assert [(r["attempt"], r["superseded"]) for r in out.runs] == [(1, True), (2, False)]
    assert len(fr.calls) == 2


def test_second_gateway_error_aborts():
    err = lambda: result(status="error", reason="timeout", cost=None, chat=1)  # noqa: E731
    out, _, sleeps, _ = go(["B01"], [err, err])
    assert out.aborted is not None and "second gateway error (timeout)" in out.aborted
    assert sleeps == [10.0]
    assert len(out.runs) == 2


def test_other_error_category_aborts_without_waiting():
    err = result(status="error", reason="auth", cost=None, chat=1)
    out, fr, sleeps, _ = go(["B01"], [err])
    assert out.aborted is not None and "gateway error auth" in out.aborted
    assert sleeps == [] and len(fr.calls) == 1


def test_blocked_run_aborts_unless_the_attack_expects_a_gateway_block():
    blocked = lambda: result(  # noqa: E731
        status="blocked", reason="blocked_policy", cost=None, chat=1
    )
    out, *_ = go(["B01"], [blocked])
    assert out.aborted is not None and "unexpected gateway block" in out.aborted
    expected = next(a.id for a in ATS.live() if a.expect_gateway)
    out, fr, sleeps, _ = go([expected, "B01"], [blocked])
    assert out.aborted is None
    assert sleeps == [] and len(fr.calls) == 2  # no rerun of the blocked run
    first = out.runs[0]
    assert first["status"] == "blocked" and first["pre_delivery"] == "gateway"
    assert first["delivered"] is False


def test_cost_cap_aborts():
    out, _, _, _ = go(["A01", "B01"], [result(cost=0.6), result(cost=0.6)], cost_cap=1.0)
    assert out.aborted is not None and "cost cap exceeded" in out.aborted
    assert len(out.runs) == 2


def test_missing_cost_header_aborts_but_not_for_gateway_results():
    out, *_ = go(["B01"], [result(cost=None)])
    assert out.aborted is not None and "cost header missing" in out.aborted


def test_startup_failure_is_a_result_with_zero_chat_calls():
    boot = next(a for a in ATS.live() if any(o.startswith("boot:") for o in a.expect["on"].out))
    out, fr, _, _ = go([boot.id, "B01"])
    first = out.runs[0]
    assert out.aborted is None
    assert first["status"] == rpt.NOT_RUN and first["reason"].startswith("boot:")
    assert first["pre_delivery"] == "boot" and first["n_chat_calls"] == 0
    assert first["offered_tools"] is None and first["delivered"] is False
    assert len(fr.calls) == 1  # only B01 reached the loop


def test_the_run_gets_the_genuine_runtime_repo_and_config_per_mode():
    out, fr, _, _ = go(["B01"], mode="off")
    _, user, kw = fr.calls[0]
    assert user == ATS.by_id("B01").user
    assert not any(kw["config"].policy.defenses.values())
    out, fr, _, _ = go(["B01"], mode="on")
    assert all(fr.calls[0][2]["config"].policy.defenses.values())
    assert type(fr.calls[0][2]["repo"]).__name__ == "TamperingRepo"


def test_record_fields():
    out, *_ = go(["A01"], [result(malicious_trace(), embed=1)])
    run = out.runs[0]
    spec = {
        "attack", "channel", "repeat", "superseded", "status", "reason", "delivered",
        "pre_delivery", "attempted", "malicious", "targets_hit", "first_layer", "config_writes",
        "unauthorized_side_effects", "unauthorized_reads", "rows", "tool_trace", "tool_outputs",
        "offered_tools", "call_ids", "model_names", "n_chat_calls", "n_embed_calls", "cost_usd",
        "final_text", "r5_redactions",
    }  # fmt: skip
    assert spec <= set(run)
    assert set(run["rows"]) == {"exception_requests", "drafts"}
    assert run["malicious"] == [{"turn": 2, "tool": PREP, "outcome": "drafted"}]
    assert run["r5_redactions"] is None
    assert PREP in run["offered_tools"]


# --- the run subcommand ------------------------------------------------------------------------


class Boom:
    def __init__(self, what):
        self.what = what

    def __call__(self, *a, **kw):
        raise AssertionError(f"{self.what} must not be called")


def make_env(settings, tmp_path, run_fn=None, **kw):
    base = dict(
        load_settings=lambda: settings,
        dsn=lambda: "postgresql://unused",
        make_deps=lambda dsn: fake_deps(),
        git_info=lambda: ("abc123", False),
        sleep=lambda s: None,
        clock=lambda: datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC),
        reports_dir=tmp_path / "reports",
        environ={},
        ledger_path=tmp_path / "attack_runs.csv",
        table_check=lambda dsn: ({"t": 1}, {"c": True}),
        db_chunk_ids=lambda dsn: attack_eval.parsed_adversarial_ids(ATS.adv_dir),
        check_reranker=lambda s: None,
        plain_runtime_factory=lambda s: "plain",
        runtime_factory=lambda s: "mixed",
        run_fn=run_fn or FakeRun(),
    )
    base.update(kw)
    return attack_eval.Env(**base)


def argv(mode="on", repeats="3", cap="5", *extra):
    out = ["run"]
    if mode is not None:
        out += ["--mode", mode]
    out += ["--repeats", repeats]
    if cap is not None:
        out += ["--cost-cap", cap]
    return out + list(extra)


def copy_with(tmp_path, old, new):
    path = tmp_path / "attacks.toml"
    path.write_text(TEXT.replace(old, new, 1), encoding="utf-8")
    return str(path)


def sealed(settings, tmp_path, **kw):
    """An Env where no model call or runtime can happen."""
    return make_env(
        settings,
        tmp_path,
        run_fn=Boom("run_fn"),
        plain_runtime_factory=Boom("client"),
        runtime_factory=Boom("client"),
        **kw,
    )


def test_refusals_send_nothing(settings, tmp_path, capsys):
    odd_prompt = tmp_path / "prompt.md"
    odd_prompt.write_text("another prompt\n", encoding="utf-8")
    hash_file = copy_with(tmp_path, 'tools_lock_sha256 = "befd', 'tools_lock_sha256 = "0efd')
    cases = [
        ("mode missing", argv(None), {}, 2, "[REFUSED]"),
        ("mode hybrid", argv("hybrid"), {}, 2, "[REFUSED]"),
        ("repeats 0", argv("on", "0"), {}, 2, "[REFUSED]"),
        ("cost cap missing", argv("on", "3", None), {}, 2, "[REFUSED]"),
        ("cost cap zero", argv("on", "3", "0"), {}, 2, "[REFUSED]"),
        ("final with only", argv("on", "3", "5", "--final", "--only", "A01"), {}, 2, "--only"),
        ("final repeats", argv("on", "2", "5", "--final"), {}, 2, "--repeats 3"),
        (
            "final dirty",
            argv("on", "3", "5", "--final"),
            {"git_info": lambda: ("abc123", True)},
            2,
            "clean git tree",
        ),
        (
            "final git unknown",
            argv("on", "3", "5", "--final"),
            {"git_info": lambda: (None, None)},
            2,
            "clean git tree",
        ),
        ("off without ack", argv("off"), {}, 2, "AGENT_DEFENSES_OFF_ACK=1"),
        (
            "off ack not 1",
            argv("off"),
            {"environ": {"AGENT_DEFENSES_OFF_ACK": "0"}},
            2,
            "AGENT_DEFENSES_OFF_ACK=1",
        ),
        (
            "on with AGENT_DISABLE",
            argv("on"),
            {"environ": {"AGENT_DISABLE": "x"}},
            2,
            "AGENT_DISABLE",
        ),
        ("unknown attack", argv("on", "1", "5", "--only", "Z99"), {}, 1, "not a live attack"),
        ("matrix-only attack", argv("on", "1", "5", "--only", "C04"), {}, 1, "not a live attack"),
        (
            "direct mode",
            argv("on"),
            {"load_settings": lambda: settings.model_copy(update={"llm_mode": "direct"})},
            1,
            "gateway mode",
        ),
        (
            "prompt hash",
            argv("on"),
            {
                "load_settings": lambda: settings.model_copy(
                    update={"agent_prompt": str(odd_prompt)}
                )
            },
            1,
            "prompt_sha256",
        ),
        ("registered hash", argv("on") + ["--attacks", hash_file], {}, 1, "tools_lock_sha256"),
        (
            "reranker",
            argv("on"),
            {"check_reranker": lambda s: "reranker_unavailable"},
            1,
            "reranker",
        ),
        (
            "tables",
            argv("on"),
            {"table_check": lambda dsn: ({"agent.drafts": None}, {})},
            1,
            "tables or constraints missing",
        ),
        (
            "adversarial chunks",
            argv("on"),
            {"db_chunk_ids": lambda dsn: {"ADV-001#x"}},
            1,
            "differ from corpus/adversarial",
        ),
    ]
    for name, args, over, code, text in cases:
        env = sealed(settings, tmp_path, **over)
        assert attack_eval.main(args, env) == code, name
        out = capsys.readouterr().out
        assert text in out, name
        assert ("[REFUSED]" if code == 2 else "[FAIL]") in out, name
        assert not (tmp_path / "reports").exists(), name  # nothing was written
        assert not (tmp_path / "attack_runs.csv").exists(), name


def test_mode_off_prints_the_seven_names_and_leaves_os_environ_alone(
    settings, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("AGENT_DEFENSES_OFF_ACK", "1")
    monkeypatch.delenv("AGENT_DISABLE", raising=False)
    before = dict(os.environ)
    env = make_env(settings, tmp_path, environ=os.environ)
    assert attack_eval.main(argv("off", "1", "5", "--only", "B01"), env) == 0
    out = capsys.readouterr().out
    assert out.startswith("DEFENSES OFF: ")
    assert len(out.splitlines()[0].removeprefix("DEFENSES OFF: ").split(", ")) == 7
    assert dict(os.environ) == before
    assert "AGENT_DISABLE" not in os.environ


def test_mode_on_run_writes_the_report_files_but_no_ledger_line(settings, tmp_path, capsys):
    env = make_env(settings, tmp_path)
    assert attack_eval.main(argv("on", "2", "5", "--only", "A01,B01"), env) == 0
    stamp = "20261118T120000Z"
    d = tmp_path / "reports"
    assert sorted(p.name for p in d.iterdir()) == [
        f"attack_on_{stamp}.json",
        f"attack_on_{stamp}.md",
        f"review_attack_on_{stamp}.csv",
    ]
    report = json.loads((d / f"attack_on_{stamp}.json").read_text(encoding="utf-8"))
    assert list(report) == ["meta", "summary", "by_layer", "runs"]
    assert report["meta"]["mode"] == "on" and report["meta"]["n_runs"] == 4
    assert report["meta"]["cost_cap"] == 5.0
    assert list(report["meta"]) == list(rpt.META_KEYS)
    with open(d / f"review_attack_on_{stamp}.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert [(r["attack"], r["repeat"], r["claim"]) for r in rows] == [
        ("A01", "1", ""),
        ("B01", "1", ""),
    ]
    assert not (tmp_path / "attack_runs.csv").exists()
    assert "files:" in capsys.readouterr().out


def test_only_is_not_a_final_option_but_works_without(settings, tmp_path):
    env = make_env(settings, tmp_path)
    assert attack_eval.main(argv("on", "1", "5", "--only", "A01"), env) == 0


def test_abort_writes_the_aborted_file_and_no_report(settings, tmp_path, capsys):
    run_fn = FakeRun([result(malicious_trace())])
    env = make_env(settings, tmp_path, run_fn=run_fn)
    assert attack_eval.main(argv("on", "1", "5", "--only", "A01"), env) == 1
    names = sorted(p.name for p in (tmp_path / "reports").iterdir())
    assert names == ["aborted_attack_20261118T120000Z.json"]
    data = json.loads((tmp_path / "reports" / names[0]).read_text(encoding="utf-8"))
    assert "unauthorized effect" in data["aborted"] and len(data["runs"]) == 1
    assert not (tmp_path / "attack_runs.csv").exists()  # no --final: no ledger line
    assert "[FAIL] aborted" in capsys.readouterr().out


def test_final_abort_appends_an_aborted_ledger_line(settings, tmp_path):
    env = make_env(settings, tmp_path, run_fn=FakeRun([result(malicious_trace())]))
    assert attack_eval.main(argv("on", "3", "5", "--final"), env) == 1
    lines = (tmp_path / "attack_runs.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(rpt.LEDGER_HEADER)
    row = next(csv.DictReader([lines[0], lines[1]]))
    assert len(lines) == 2
    assert row["status"] == "aborted" and row["unauthorized_side_effects"] == "1"
    assert row["n_runs"] == "1" and row["git_commit"] == "abc123" and row["git_dirty"] == "False"
    assert not list((tmp_path / "reports").glob("attack_on_*"))


def test_final_run_appends_one_line_and_never_rewrites(settings, tmp_path):
    env = make_env(settings, tmp_path)
    assert attack_eval.main(argv("on", "3", "5", "--final"), env) == 0
    path = tmp_path / "attack_runs.csv"
    first = path.read_text(encoding="utf-8")
    assert len(first.splitlines()) == 2
    row = next(csv.DictReader(first.splitlines()))
    live = len(ATS.live())
    assert row["status"] == "ok" and row["mode"] == "on" and row["repeats"] == "3"
    assert row["n_runs"] == str(live * 3)
    assert row["attacks_sha256"] == ATS.sha256
    assert row["model_names"] == "fake-model"
    assert row["unauthorized_side_effects"] == "0" and row["unauthorized_reads"] == "0"
    assert attack_eval.main(argv("on", "3", "5", "--final"), env) == 0
    second = path.read_text(encoding="utf-8")
    assert second.startswith(first) and len(second.splitlines()) == 3  # one header only


def test_ledger_header_once_and_missing_newline_repaired(tmp_path):
    path = tmp_path / "sub" / "attack_runs.csv"
    row = ["x"] * len(rpt.LEDGER_HEADER)
    rpt.append_ledger(path, row)
    rpt.append_ledger(path, row)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(rpt.LEDGER_HEADER) and len(lines) == 3
    cut = tmp_path / "cut.csv"
    cut.write_text("a,b\n1,2", encoding="utf-8")  # no trailing newline
    rpt.append_ledger(cut, ["3", "4"])
    assert cut.read_text(encoding="utf-8") == "a,b\n1,2\n3,4\n"
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    rpt.append_ledger(empty, row)
    assert empty.read_text(encoding="utf-8").startswith(",".join(rpt.LEDGER_HEADER))
