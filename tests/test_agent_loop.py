"""The agent loop against scripted clients and the recorded gateway fixtures. No network."""

import ast
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from agent_fakes import FakeRepo, agent_config, exc_args
from agent_loop_fakes import (
    CHUNK_1,
    ScriptedClient,
    chat_bodies,
    final_reply,
    recorded_message,
    replay_client,
    runtime_for,
    tc,
    tool_reply,
)
from rag_agent import llm_client
from rag_agent import messages as d6
from rag_agent.agent import loop
from rag_agent.agent import messages as msg
from rag_agent.agent.errors import (
    AgentStartupError,
    DefensesOffUnacknowledged,
    FactsMissingKey,
    PolicyInvalid,
    SpecInvalid,
    ToolsLockMismatch,
)
from rag_agent.config import ConfigError
from rag_agent.errors import UpstreamTimeoutError

OWN = "DMC-2026-00101"  # br-01 submitted it, uw-01 is assigned
L_MINOR = "DMC-2026-00102"
BR02_LOAN = "DMC-2026-00103"
UNKNOWN = "DMC-2026-99998"
ROOT = Path(__file__).resolve().parents[1]


def run(settings, client, question, user="uw-01", repo=None, config=None):
    return loop.run_agent(
        question,
        user,
        config=config or agent_config(),
        runtime=runtime_for(settings, client),
        repo=repo or FakeRepo(),
        log=False,
    )


def status_call(loan=OWN, call_id="t1"):
    return tc("get_loan_status", call_id, loan_number=loan)


def tool_messages(request):
    return [m for m in request["messages"] if m["role"] == "tool"]


# --- fixture replay --------------------------------------------------------------------------


def test_fixture_replay_echoes_assistant_message_verbatim(settings):
    client, seen = replay_client(
        settings, ("gateway_ok", "agent_tool_call"), ("gateway_ok", "agent_final")
    )
    repo = FakeRepo()
    res = run(settings, client, "What is the maximum TDS ratio?", repo=repo)
    first, second = chat_bodies(seen)
    assert [m["role"] for m in first["messages"]] == ["system", "user"]
    assert len(first["tools"]) == 5 and first["tool_choice"] == "auto"
    assert first["max_tokens"] == settings.agent_max_tokens
    assert not {"temperature", "top_p", "seed"} & set(first)
    # the assistant message goes back field for field, extra fields included, nothing added
    assert second["messages"][2] == recorded_message("agent_tool_call")
    call_id = recorded_message("agent_tool_call")["tool_calls"][0]["id"]
    tool_message = second["messages"][3]
    assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == call_id
    assert json.loads(tool_message["content"])["status"] == "ok"
    assert len(second["messages"]) == 4
    assert (res.status, res.reason, res.finish_reason) == ("completed", None, "stop")
    assert res.final_text == recorded_message("agent_final")["content"] == res.message
    assert res.call_ids == [
        "64d81bf6-d6bd-4d21-b6fd-bf012f88da1a",
        "bd8b1af2-fab1-472e-a240-99ea9352eac8",
    ]
    assert res.model_names == ["anthropic/claude-haiku-5-5"]
    assert res.cost_usd == pytest.approx(8.63e-05 + 0.0001157)
    assert (res.n_chat_calls, res.n_tool_calls, res.n_embed_calls) == (2, 1, 1)
    assert [r.url.path.rsplit("/", 1)[-1] for r in seen] == [
        "completions",
        "embeddings",
        "completions",
    ]
    assert res.tool_trace[0]["tool"] == "search_policy" and res.tool_trace[0]["turn"] == 1
    assert res.sources[0] == ["S1", CHUNK_1]
    # the recorded answer cites nothing: recorded, and the status does not change
    assert res.cited_markers == [] and res.uncited_units
    assert [e["step"] for e in repo.events] == [1]


def test_budget_error_at_second_call_is_not_retried_and_stays_on_the_gateway(settings, monkeypatch):
    settings.llm_max_retries = 3
    client, seen = replay_client(
        settings, ("gateway_ok", "agent_tool_call"), ("gateway_errors", "budget_exceeded")
    )

    def no_new_client(*a, **k):
        raise AssertionError("a client was constructed during the run")

    monkeypatch.setattr(llm_client, "OpenAI", no_new_client)
    res = run(settings, client, "What is the maximum TDS ratio?")
    assert (res.status, res.reason) == ("error", "budget_exceeded")
    assert res.message == d6.B5
    assert len(chat_bodies(seen)) == 2 and res.n_chat_calls == 2  # P18: zero retries
    assert {r.url.host for r in seen} == {"gateway.invalid"}
    assert settings.openai_base_url == "http://gateway.invalid:4000/v1"
    assert str(client._chat.base_url).startswith("http://gateway.invalid:4000/v1")
    dump = repr(asdict(res))
    assert "d6-fixture-budget" not in dump and "sk-" not in dump


def test_blocked_tool_message_at_second_call(settings):
    client, seen = replay_client(
        settings, ("gateway_ok", "agent_tool_call"), ("gateway_errors", "blocked_tool_message")
    )
    res = run(settings, client, "What is the maximum TDS ratio?")
    assert (res.status, res.reason, res.message) == ("blocked", "blocked_policy", d6.B4)
    assert len(chat_bodies(seen)) == 2
    assert res.call_ids[-1] == "fff9656a-aa6e-4da0-806e-7c69ab4a78d8"
    assert "R10" not in repr(asdict(res)) and "policy_blocked" not in repr(asdict(res))


# --- tool messages and limits ----------------------------------------------------------------


def test_every_tool_call_id_gets_exactly_one_tool_message(settings):
    calls = [
        status_call(OWN, "t1"),
        status_call(BR02_LOAN, "t2"),  # not br-01's loan: denied
        status_call(OWN, "t3"),
        status_call(OWN, "t4"),  # fourth call in one message: too_many_calls
    ]
    client = ScriptedClient(tool_reply(*calls), final_reply("I could not open one of the loans."))
    res = run(settings, client, "Show my loans.", user="br-01")
    sent = tool_messages(client.requests[1])
    assert [m["tool_call_id"] for m in sent] == ["t1", "t2", "t3", "t4"]
    assert [t["reason"] for t in res.tool_trace] == [
        None,
        "not_found_or_not_permitted",
        None,
        "too_many_calls",
    ]
    assert res.status == "completed" and (res.n_tool_calls, res.n_denials) == (4, 2)
    assert client.requests[1]["messages"][2]["tool_calls"] == calls


def test_p11_three_calls_in_each_of_two_messages_are_not_too_many(settings):
    first = [status_call(OWN, f"a{i}") for i in range(3)]
    second = [status_call(OWN, f"b{i}") for i in range(3)]
    client = ScriptedClient(tool_reply(*first), tool_reply(*second), final_reply("All done here."))
    res = run(settings, client, "Check the loan.")
    assert [t["status"] for t in res.tool_trace] == ["ok"] * 6
    assert [t["turn"] for t in res.tool_trace] == [1, 1, 1, 2, 2, 2]
    assert res.status == "completed" and res.n_denials == 0


def test_p12_step_limit_after_eight_chat_calls(settings):
    client = ScriptedClient(*[tool_reply(status_call(OWN, f"t{i}")) for i in range(9)])
    res = run(settings, client, "Keep checking the loan.")
    assert (res.status, res.reason, res.message) == ("step_limit", "max_chat_calls", msg.C3)
    assert len(client.requests) == 8 and res.n_chat_calls == 8 and res.n_tool_calls == 8
    assert res.finish_reason == "tool_calls"


def test_step_limit_when_tool_calls_exceed_the_limit(settings):
    turns = [tool_reply(*[status_call(OWN, f"t{n}-{i}") for i in range(3)]) for n in range(6)]
    client = ScriptedClient(*turns)
    res = run(settings, client, "Keep checking the loan.")
    assert (res.status, res.reason) == ("step_limit", "max_tool_calls")
    # 12 calls after four messages is at the limit, not over it; the fifth message goes over
    assert len(client.requests) == 5 and res.n_tool_calls == 15


def test_p13_policy_stopped_without_another_chat_call(settings):
    denied = [status_call(UNKNOWN, f"t{i}") for i in range(3)]
    client = ScriptedClient(tool_reply(*denied), final_reply("never requested"))
    res = run(settings, client, "Open that loan.")
    assert (res.status, res.reason, res.message) == ("policy_stopped", "too_many_denials", msg.C2)
    assert len(client.requests) == 1 and res.n_denials == 3 and res.final_text == ""


def test_limits_come_from_the_policy(settings):
    cfg = agent_config()
    limits = replace(cfg.policy.limits, max_chat_calls=2, max_denials=1)
    cfg = replace(cfg, policy=replace(cfg.policy, limits=limits))
    client = ScriptedClient(*[tool_reply(status_call(OWN, f"t{i}")) for i in range(3)])
    res = run(settings, client, "Keep checking the loan.", config=cfg)
    assert (res.status, res.reason) == ("step_limit", "max_chat_calls")
    assert len(client.requests) == 2
    client = ScriptedClient(tool_reply(status_call(UNKNOWN)), final_reply("never requested"))
    res = run(settings, client, "Open that loan.", config=cfg)
    assert (res.status, res.reason) == ("policy_stopped", "too_many_denials")


# --- errors ----------------------------------------------------------------------------------


def test_gateway_error_at_the_first_call(settings):
    client = ScriptedClient(UpstreamTimeoutError("request timed out", None))
    res = run(settings, client, "What is the maximum TDS ratio?")
    assert (res.status, res.reason, res.message) == ("error", "timeout", d6.B7)
    assert res.n_chat_calls == 1 and client.requests[0]["kw"] == {
        "tool_choice": "auto",
        "max_tokens": settings.agent_max_tokens,
    }


def test_other_exceptions_propagate(settings):
    client = ScriptedClient(RuntimeError("bug"))
    with pytest.raises(RuntimeError):
        run(settings, client, "What is the maximum TDS ratio?")


@pytest.mark.parametrize(
    ("user", "question", "reason", "message"),
    [
        ("zz-99", "What is the limit?", "unknown_user", msg.C4),
        ("uw-01", "   ", "empty", d6.B11a),
        ("uw-01", "x" * 1001, "too_long", d6.B11b),
    ],
)
def test_rejected_input_makes_no_gateway_call(settings, user, question, reason, message):
    client = ScriptedClient()
    res = loop.run_agent(
        question, user, config=agent_config(), runtime=runtime_for(settings, client), log=False
    )
    assert (res.status, res.reason, res.message) == ("rejected_input", reason, message)
    assert client.requests == [] and client.embeds == [] and res.n_chat_calls == 0
    assert len(res.run_id) == 12


# --- drafts and the approval boundary --------------------------------------------------------


def draft_script(final_text):
    return ScriptedClient(
        tool_reply(tc("search_policy", "t1", query="TDS limit exceptions")),
        tool_reply(
            tc(
                "prepare_exception_request",
                "t2",
                **exc_args(L_MINOR, supporting_chunk_ids=[CHUNK_1]),
            )
        ),
        final_reply(final_text),
    )


def test_awaiting_raise_continues_to_a_final_answer(settings):
    repo = FakeRepo()
    client = draft_script("Draft ER-000001 is ready for the underwriter to review [S1].")
    res = run(settings, client, f"Prepare a TDS exception for loan {L_MINOR}.", repo=repo)
    assert len(client.requests) == 3  # the loop went on after the draft
    assert res.status == "awaiting_raise" and res.reason is None
    assert res.message == f"{res.final_text}\n\n{msg.C1}"
    assert res.exception_request_ids == ["ER-000001"]
    assert [r["status"] for r in repo.requests] == ["awaiting_raise"]
    assert repo.requests[0]["raised_by"] is None and repo.requests[0]["decided_by"] is None
    draft = res.tool_trace[1]
    assert (draft["status"], draft["rule_stage"], draft["reached_executor"]) == (
        "awaiting_raise",
        8,
        False,
    )
    assert draft["turn"] == 2 and [e["step"] for e in repo.events] == [1, 2]
    assert res.sources == [["S1", CHUNK_1], ["S2", "DOC-002#s2"]]
    assert res.cited_markers == ["S1"] and res.unknown_markers == [] and res.n_embed_calls == 1


def test_tool_outputs_has_one_entry_per_tool_call_with_the_outcome_data(settings):
    calls = [status_call(OWN, "t1"), status_call(BR02_LOAN, "t2"), tc("nope", "t3")]
    client = ScriptedClient(tool_reply(*calls), final_reply("Done."))
    res = run(settings, client, "Show my loans.", user="br-01")
    out = res.tool_outputs
    assert [(o["turn"], o["tool"], o["status"]) for o in out] == [
        (t["turn"], t["tool"], t["status"]) for t in res.tool_trace
    ]
    assert [o["tool"] for o in out] == ["get_loan_status", "get_loan_status", "unknown"]
    assert out[0]["data"]["loan_number"] == OWN and out[0]["data"]["stage"]
    assert out[1]["status"] == "denied" and "stage" not in out[1]["data"]


def test_run_log_line_has_no_tool_outputs(settings, tmp_path):
    client = ScriptedClient(tool_reply(status_call(OWN, "t1")), final_reply("Done."))
    res = loop.run_agent(
        "Show my loan.",
        "br-01",
        config=agent_config(),
        runtime=runtime_for(settings, client),
        repo=FakeRepo(),
        log=True,
        log_dir=str(tmp_path),
    )
    assert len(res.tool_outputs) == 1
    (path,) = tmp_path.glob("runs_*.jsonl")
    line = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert line["run_id"] == res.run_id and "tool_outputs" not in line


def test_matching_a_pending_approval_request_ends_completed_without_c1(settings):
    repo = FakeRepo()
    repo._new_request(
        {
            "loan_number": L_MINOR,
            "exception_type": "gds_tds_ratio",
            "required_authority": "senior_underwriter",
            "overage_pts": None,
        },
        "pending_approval",
    )
    client = draft_script("Request ER-000001 is already waiting for a decision.")
    res = run(settings, client, f"Prepare a TDS exception for loan {L_MINOR}.", repo=repo)
    assert res.tool_trace[1]["status"] == "pending_approval"
    assert res.status == "completed" and msg.C1 not in res.message
    assert res.exception_request_ids == ["ER-000001"] and len(repo.requests) == 1


def test_approvals_module_is_not_reachable_from_the_loop():
    src = ROOT / "src"
    seen: set[str] = set()
    todo = ["rag_agent.agent.loop"]
    while todo:
        module = todo.pop()
        base = src / module.replace(".", "/")
        path = (
            base.with_suffix(".py") if base.with_suffix(".py").is_file() else base / "__init__.py"
        )
        if module in seen or not path.is_file():
            continue
        seen.add(module)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                todo += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                todo += [node.module, *[f"{node.module}.{a.name}" for a in node.names]]
    assert {"rag_agent.agent.loop", "rag_agent.agent.policy", "rag_agent.agent.tools"} <= seen
    assert "rag_agent.agent.approvals" not in seen


# --- citations, tools sent, finish_reason ----------------------------------------------------


def test_unknown_and_missing_markers_do_not_change_the_status(settings):
    client = ScriptedClient(final_reply("The limit is set by policy [S9]. Ask a specialist too."))
    res = run(settings, client, "What is the maximum TDS ratio?")
    assert res.status == "completed" and res.message == res.final_text
    assert (res.cited_markers, res.unknown_markers) == ([], ["S9"])
    assert res.uncited_units == ["Ask a specialist too."]


def test_length_finish_reason_is_recorded_and_still_completed(settings):
    client = ScriptedClient(final_reply("The answer was cut", finish_reason="length"))
    res = run(settings, client, "What is the maximum TDS ratio?")
    assert (res.status, res.finish_reason) == ("completed", "length")


def test_role_allowlist_decides_the_tools_sent(settings):
    client = ScriptedClient(final_reply("Hello there, how can I help?"))
    run(settings, client, "Hello there.", user="br-01")
    names = [t["function"]["name"] for t in client.requests[0]["tools"]]
    assert len(names) == 4 and "prepare_exception_request" not in names
    off = agent_config({"AGENT_DISABLE": "role_allowlist", "AGENT_DEFENSES_OFF_ACK": "1"})
    client = ScriptedClient(final_reply("Hello there, how can I help?"))
    res = run(settings, client, "Hello there.", user="br-01", config=off)
    assert len(client.requests[0]["tools"]) == 5
    assert res.defenses["role_allowlist"] is False


def test_system_prompt_and_hashes(settings):
    client = ScriptedClient(final_reply("Hello there, how can I help?"))
    cfg = agent_config()
    res = run(settings, client, "  Hello there.  ", config=cfg)
    system, user = client.requests[0]["messages"]
    assert system["content"].startswith("You are the operations assistant for Demo Mortgage Co.")
    assert user == {"role": "user", "content": "Hello there."}
    assert res.policy_sha256 == cfg.policy.sha256
    lock = (ROOT / "src/rag_agent/agent/tools.lock").read_text(encoding="utf-8")
    assert f'total = "{res.tools_lock_sha256}"' in lock


# --- startup ---------------------------------------------------------------------------------


@pytest.fixture
def threshold_file(tmp_path):
    p = tmp_path / "threshold.toml"
    p.write_text('signal = "rerank_top1"\nthreshold = 0.0621\n')
    return p


@pytest.fixture
def fresh_startup(monkeypatch):
    monkeypatch.setattr(loop, "_STARTED", None)
    monkeypatch.setattr(loop.boot, "load_agent_config", agent_config)


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (ToolsLockMismatch(changed=["search_policy"]), "tools_lock_mismatch"),
        (PolicyInvalid("bad"), "policy_invalid"),
        (FactsMissingKey("missing"), "facts_missing_key"),
        (DefensesOffUnacknowledged("off"), "defenses_off_unacknowledged"),
        (SpecInvalid("bad"), "specs_invalid"),
    ],
)
def test_startup_error_codes_before_any_client(monkeypatch, settings, fresh_startup, exc, code):
    def boom():
        raise exc

    built = []
    monkeypatch.setattr(loop.boot, "load_agent_config", boom)
    with pytest.raises(AgentStartupError) as info:
        loop.startup(settings, client_factory=built.append)
    assert info.value.code == code and built == [] and loop._STARTED is None


def test_startup_config_errors(monkeypatch, settings, fresh_startup, threshold_file, tmp_path):
    built = []

    def bad_settings():
        raise ConfigError("invalid configuration")

    monkeypatch.setattr(loop, "load_settings", bad_settings)
    with pytest.raises(AgentStartupError) as info:
        loop.startup(client_factory=built.append, threshold_path=threshold_file)
    assert info.value.code == "config_invalid"
    with pytest.raises(AgentStartupError) as info:
        loop.startup(settings, client_factory=built.append, threshold_path=tmp_path / "none.toml")
    assert info.value.code == "config_invalid"
    settings.agent_prompt = "src/rag_agent/prompts/missing.md"
    with pytest.raises(AgentStartupError) as info:
        loop.startup(settings, client_factory=built.append, threshold_path=threshold_file)
    assert info.value.code == "config_invalid" and built == []


def test_startup_result_is_used_by_run_agent(settings, fresh_startup, threshold_file):
    client = ScriptedClient(final_reply("Hello there, how can I help?"))
    built = []
    config, runtime = loop.startup(
        settings, client=client, client_factory=built.append, threshold_path=threshold_file
    )
    assert runtime.client is client and built == []
    res = loop.run_agent("Hello there.", "uw-01", repo=FakeRepo(), log=False)
    assert res.status == "completed" and len(client.requests) == 1
    assert res.policy_sha256 == config.policy.sha256
