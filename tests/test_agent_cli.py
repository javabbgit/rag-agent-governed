"""scripts/agent.py: banner, rendering and exit codes. No network (the loop is replaced)."""

import json

import pytest

import agent as cli
from rag_agent.agent import loop
from rag_agent.agent.errors import AgentStartupError
from rag_agent.agent.loop import AgentResult

ALL_ON = {"role_allowlist": True, "approval_gate": True}


def result(**over):
    base = {
        "run_id": "0123456789ab",
        "status": "awaiting_raise",
        "reason": None,
        "final_text": "Draft ER-000001 is ready [S1].",
        "message": "Draft ER-000001 is ready [S1].\n\nOnly a draft.",
        "exception_request_ids": ["ER-000001"],
        "draft_ids": ["DR-000002"],
        "tool_trace": [
            {
                "turn": 1,
                "rule_stage": 8,
                "tool": "prepare_exception_request",
                "status": "awaiting_raise",
                "reason": None,
                "reached_executor": False,
                "latency_ms": 3.2,
                "args_digest": {},
            }
        ],
        "sources": [["S1", "POL-003#gds-and-tds-limits"]],
        "model_names": ["upstream/model"],
        "cost_usd": 0.0002,
        "latency_ms_total": 1234.5,
        "n_chat_calls": 3,
        "n_tool_calls": 1,
        "defenses": dict(ALL_ON),
    }
    base.update(over)
    return AgentResult(**base)


@pytest.fixture
def fake_loop(monkeypatch):
    calls = []

    def install(outcome):
        def run_agent(question, user_id, **kw):
            calls.append((question, user_id, kw))
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(loop, "startup", lambda: None)
        monkeypatch.setattr(loop, "run_agent", run_agent)
        return calls

    return install


def test_banner_names_the_switches_that_are_off():
    assert cli.banner(ALL_ON) is None
    off = {"role_allowlist": False, "approval_gate": True, "data_envelope": False}
    assert cli.banner(off) == "DEFENSES OFF: role_allowlist, data_envelope"


def test_render_shows_every_section():
    text = cli.render(result())
    assert text.startswith("status: awaiting_raise\nreason: None\n\nDraft ER-000001")
    for part in (
        "prepare_exception_request",
        "[S1] POL-003#gds-and-tds-limits",
        "exception requests: ER-000001",
        "drafts: DR-000002",
        "models: upstream/model",
        "cost: 0.000200 USD",
        "latency: 1234 ms",
        "run 0123456789ab",
    ):
        assert part in text
    bare = cli.render(result(tool_trace=[], sources=[], cost_usd=None, draft_ids=[]))
    assert "Tool trace" not in bare and "cost: n/a" in bare and "drafts: -" in bare


def test_classified_results_exit_zero(fake_loop, capsys):
    calls = fake_loop(result(status="blocked", reason="blocked_policy", message="Blocked."))
    assert cli.main(["--as", "uw-01", "What is the limit?"]) == 0
    assert calls == [("What is the limit?", "uw-01", {})]
    out = capsys.readouterr().out
    assert out.startswith("status: blocked") and "DEFENSES OFF" not in out


def test_banner_is_the_first_line_when_a_switch_is_off(fake_loop, capsys):
    fake_loop(result(defenses={"role_allowlist": True, "approval_gate": False}))
    assert cli.main(["--as", "uw-01", "What is the limit?"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "DEFENSES OFF: approval_gate"


def test_json_output(fake_loop, capsys):
    fake_loop(result())
    assert cli.main(["--json", "--as", "uw-01", "What is the limit?"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "awaiting_raise" and data["finish_reason"] is None


@pytest.mark.parametrize(
    "outcome", [AgentStartupError("tools_lock_mismatch", "detail"), RuntimeError("secret detail")]
)
def test_startup_and_unexpected_errors_exit_one_with_one_line(fake_loop, capsys, outcome):
    fake_loop(outcome)
    assert cli.main(["--as", "uw-01", "What is the limit?"]) == 1
    out = capsys.readouterr().out
    assert len(out.splitlines()) == 1 and out.startswith("[FAIL]")
    assert "secret detail" not in out


@pytest.mark.parametrize("argv", [[], ["question only"], ["--as", "uw-01"], ["--nope"]])
def test_usage_errors_exit_two(fake_loop, capsys, argv):
    calls = fake_loop(result())
    assert cli.main(argv) == 2
    assert calls == []
