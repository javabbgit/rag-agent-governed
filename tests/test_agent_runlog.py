"""Agent run log: what is stored, what is hashed, and where it is written."""

import hashlib
import json
from pathlib import Path

from agent_fakes import FakeRepo, agent_config
from agent_loop_fakes import ScriptedClient, final_reply, runtime_for, tc, tool_reply
from rag_agent.agent import loop

L_MINOR = "DMC-2026-00102"
ROOT = Path(__file__).resolve().parents[1]
QUESTION = f"Draft a zebracrossing reply for loan {L_MINOR}."
BODY = "The quokkaparade file needs one more document."
FINAL = "The yakfestival draft was saved. It was not sent."


def logged_run(settings, repo=None, user="uw-01", question=QUESTION, **kw):
    repo = repo or FakeRepo()
    contact = repo.get_contacts(L_MINOR)[0]["contact_id"]
    client = ScriptedClient(
        tool_reply(
            tc(
                "draft_reply",
                "t1",
                loan_number=L_MINOR,
                recipient_contact_id=contact,
                subject="Narwhalsubject",
                body=BODY,
            )
        ),
        final_reply(FINAL, finish_reason="length"),
    )
    res = loop.run_agent(
        question,
        user,
        config=agent_config(),
        runtime=runtime_for(settings, client),
        repo=repo,
        **kw,
    )
    return res, repo


def read_lines(log_dir):
    files = list(Path(log_dir).glob("runs_*.jsonl"))
    assert len(files) == 1
    raw = files[0].read_text(encoding="utf-8")
    return raw, [json.loads(line) for line in raw.splitlines()]


def test_interactive_log_has_no_question_answer_or_free_text(settings, tmp_path):
    res, repo = logged_run(settings, log_dir=str(tmp_path / "logs"))
    raw, (line,) = read_lines(tmp_path / "logs")
    for word in ("zebracrossing", "quokkaparade", "yakfestival", "Narwhalsubject"):
        assert word not in raw
    assert "question" not in line and "final_text" not in line and "uncited_units" not in line
    assert line["question_sha256"] == hashlib.sha256(QUESTION.encode()).hexdigest()
    assert (line["source"], line["user_id"], line["role"]) == (
        "interactive",
        "uw-01",
        "underwriter",
    )
    assert (line["mode"], line["base_url_host"]) == ("gateway", "gateway.invalid")
    assert (line["status"], line["reason"], line["finish_reason"]) == ("completed", None, "length")
    digest = line["tool_trace"][0]["args_digest"]
    assert digest["loan_number"] == L_MINOR  # short known fields stay in clear
    assert set(digest["body"]) == {"sha256", "len"}
    assert digest["body"]["len"] == len(json.dumps(BODY))  # the digest covers the JSON form
    assert set(digest["subject"]) == {"sha256", "len"}
    assert digest == repo.events[0]["args_digest"]  # same digest as the policy_events row
    assert line["run_id"] == res.run_id == repo.events[0]["run_id"]
    assert line["draft_ids"] == res.draft_ids == ["DR-000001"]
    assert line["n_uncited_units"] == len(res.uncited_units) == 2
    assert (line["n_chat_calls"], line["n_tool_calls"], line["n_denials"]) == (2, 1, 0)
    assert line["n_embed_calls"] == 0 and line["cost_usd"] == 0.002
    assert line["latencies"]["chat_ms"] == [1.0, 1.0] and len(line["latencies"]["tools_ms"]) == 1
    assert all(line["defenses"].values())


def test_log_records_the_three_hashes(settings, tmp_path):
    cfg = agent_config()
    logged_run(settings, log_dir=str(tmp_path / "logs"))
    _, (line,) = read_lines(tmp_path / "logs")
    prompt = (ROOT / "src/rag_agent/prompts/agent_v1.md").read_bytes()
    assert line["prompt_sha256"] == hashlib.sha256(prompt).hexdigest()
    assert line["policy_sha256"] == cfg.policy.sha256
    lock = (ROOT / "src/rag_agent/agent/tools.lock").read_text(encoding="utf-8")
    assert f'total = "{line["tools_lock_sha256"]}"' in lock


def test_smoke_source_may_keep_the_text(settings, tmp_path):
    logged_run(settings, log_dir=str(tmp_path / "logs"), source="smoke")
    _, (line,) = read_lines(tmp_path / "logs")
    assert (line["source"], line["question"], line["final_text"]) == ("smoke", QUESTION, FINAL)
    assert "quokkaparade" not in json.dumps(line["tool_trace"])


def test_default_log_dir_is_the_tmp_path_from_the_environment(settings, tmp_path):
    assert settings.agent_log_dir == str(tmp_path / "agent_logs")
    logged_run(settings)
    logged_run(settings)
    _, lines = read_lines(tmp_path / "agent_logs")
    assert len(lines) == 2


def test_log_false_writes_nothing(settings, tmp_path):
    logged_run(settings, log=False)
    assert not (tmp_path / "agent_logs").exists()


def test_unknown_user_id_is_stored_as_a_digest(settings, tmp_path):
    typed = "someone-typed-this"
    res, _ = logged_run(settings, user=typed, log_dir=str(tmp_path / "logs"))
    raw, (line,) = read_lines(tmp_path / "logs")
    assert res.reason == "unknown_user" and typed not in raw
    assert line["user_id"]["len"] == len(typed) and line["role"] is None
    assert line["tool_trace"] == [] and line["finish_reason"] is None
