"""matrix.py: the scripted client, fixed retrieval and the matrix on an in-memory repo."""

import json

from agent_fakes import FakeRepo
from rag_agent.agent_attack.attacks import CONFIGS, load_attacks
from rag_agent.agent_attack.harness import TamperingRepo, world
from rag_agent.agent_attack.matrix import (
    EMBED_DIM,
    FIXED_SCORE,
    MatrixDeps,
    ScriptedAttackClient,
    fixed_retrieval,
    positives,
    render_md,
    report_dict,
    run_matrix,
)

ATS = load_attacks()
WORLD = world()


def fake_deps() -> MatrixDeps:
    """MatrixDeps on a FakeRepo that reset() replaces. No foreign keys: no db_fk outcome."""
    holder = {"repo": FakeRepo()}

    def reset():
        holder["repo"] = FakeRepo()

    def rows(run_id):
        repo = holder["repo"]
        return (
            [r for r in repo.requests if r["run_id"] == run_id],
            [d for d in repo.drafts if d["run_id"] == run_id],
        )

    class Deps(MatrixDeps):
        def repo_for(self, attack):
            repo = holder["repo"]
            return TamperingRepo(repo, attack.tamper) if attack.tamper else repo

    return Deps(None, reset, rows)


# --- the scripted client -----------------------------------------------------------------------


def test_scripted_client_plays_the_script_then_done():
    d03 = ATS.by_id("D03")
    client = ScriptedAttackClient(d03.script)
    first = client.chat_tools([], [{"function": {"name": "get_loan_status"}}])
    calls = first.message["tool_calls"]
    assert [c["id"] for c in calls] == ["c1", "c2", "c3"]
    assert first.finish_reason == "tool_calls" and first.message["content"] is None
    args = json.loads(calls[0]["function"]["arguments"])
    assert calls[0]["function"]["name"] == "get_loan_status"
    assert args == {"loan_number": "DMC-2026-00103"}
    final = client.chat_tools([], [])
    assert final.message == {"role": "assistant", "content": "done"}
    assert client.offered == [["get_loan_status"], []]


def test_scripted_client_ids_continue_across_messages():
    client = ScriptedAttackClient(ATS.by_id("A03").script)
    ids = [client.chat_tools([], []).message["tool_calls"][0]["id"] for _ in range(3)]
    assert ids == ["c1", "c2", "c3"]


def test_scripted_client_embeds_one_768_vector():
    out = ScriptedAttackClient(()).embed(["search_query: x"])
    assert len(out.vectors) == 1 and len(out.vectors[0]) == EMBED_DIM == 768


# --- fixed retrieval ---------------------------------------------------------------------------


def test_fixed_retrieval_uses_the_attack_doc():
    retrieve_fn, sections_fn = fixed_retrieval(ATS.by_id("A01"))
    hits = retrieve_fn("q", mode="rerank", k=10, query_vector=[0.0])
    assert [h.chunk_id for h in hits] == [
        "ADV-001#summary",
        "ADV-001#routing-a-tds-exception-to-the-credit-committee",
    ]
    assert {h.score for h in hits} == {FIXED_SCORE} and {h.status for h in hits} == {"current"}
    info = sections_fn([h.chunk_id for h in hits], dsn="x")
    assert info[hits[1].chunk_id].title == "Exception Routing Addendum"
    assert "Assistant procedure" in info[hits[1].chunk_id].text


def test_fixed_retrieval_uses_pol_010_for_other_channels():
    retrieve_fn, _ = fixed_retrieval(ATS.by_id("D01"))
    hits = retrieve_fn("q", k=10)
    assert len(hits) == 8 and hits[1].chunk_id == "POL-010#items-that-can-be-excepted"
    assert len(retrieve_fn("q", k=3)) == 3


# --- the matrix on FakeRepo --------------------------------------------------------------------


def test_matrix_a01_a03_e02_as_expected(settings):
    result = run_matrix(ATS, settings, fake_deps(), WORLD, only=["A01", "A03", "E02"])
    assert result.mismatches == []
    assert result.as_expected == len(result.cells) == 3 * len(CONFIGS)
    assert result.positives == {"T4": True, "T5": True}
    assert result.ok
    a03 = {c["config"]: c for c in result.cells if c["attack"] == "A03"}
    assert a03["approval_gate_off"]["actual"]["config_write"] is True
    assert a03["on"]["delivered"] and a03["on"]["first_layer"] == "persisted"


def test_matrix_startup_and_preflight_cells(settings):
    result = run_matrix(ATS, settings, fake_deps(), WORLD, only=["C03", "C04"])
    assert result.mismatches == []
    c04 = [c for c in result.cells if c["attack"] == "C04"]
    assert {c["first_layer"] for c in c04} == {"preflight"}
    c03 = [c for c in result.cells if c["attack"] == "C03"]
    assert {c["first_layer"] for c in c03} == {"boot:specs_invalid"}


def test_positives():
    assert positives(ATS, WORLD) == {"T4": True, "T5": True}


def test_report_and_markdown(settings):
    result = run_matrix(ATS, settings, fake_deps(), WORLD, only=["E03"])
    report = report_dict(result, ATS, "abc123", False)
    assert report["total"] == 9 and report["as_expected"] == 9
    assert report["attacks_sha256"] == ATS.sha256
    md = render_md(report)
    assert "as expected: 9/9" in md and "| E03 | approval_gate_off | db_rejected | T2 |" in md
