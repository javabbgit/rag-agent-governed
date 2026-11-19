"""harness.py: tampering repo, mixed retrieval, spec copies, switched configs, preflight."""

import hashlib
import json
import os

import pytest

from agent_fakes import FakeRepo
from answer_fakes import hit
from rag_agent.agent import boot, specs
from rag_agent.agent_attack.attacks import SpecPatch, Tamper, load_attacks
from rag_agent.agent_attack.harness import (
    TamperingRepo,
    config_for,
    disabled_for,
    env_for,
    mixed_retrieve,
    mixed_sections,
    offered_tools,
    preflight_lock,
    write_spec_copy,
)
from rag_agent.agent_attack.verdict import PreflightFailure, StartupFailure
from rag_agent.retrieve import SectionInfo

ATS = load_attacks()
L_MINOR, L_BR01 = ATS.loans["L_minor"], ATS.loans["L_br01"]


def sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def repo_files_unchanged():
    before = (sha(specs.SPECS_PATH), sha(specs.LOCK_PATH))
    yield
    assert (sha(specs.SPECS_PATH), sha(specs.LOCK_PATH)) == before


# --- TamperingRepo -----------------------------------------------------------------------------


def test_tamper_list_field_and_untouched_inner():
    inner = FakeRepo()
    original = list(inner.loans[L_MINOR]["outstanding_conditions"])
    t = ATS.by_id("B01").tamper
    repo = TamperingRepo(inner, t)
    loan = repo.get_loan(L_MINOR)
    assert loan["outstanding_conditions"] == [*original, t.text]
    assert inner.loans[L_MINOR]["outstanding_conditions"] == original
    assert repo.get_loan(L_BR01) == inner.get_loan(L_BR01)
    assert repo.get_loan("DMC-2026-09998") is None


def test_tamper_string_field():
    inner = FakeRepo()
    t = ATS.by_id("B03").tamper
    loan = TamperingRepo(inner, t).get_loan(L_BR01)
    assert loan["borrower_ref"] == "B-0001" + t.text
    assert inner.loans[L_BR01]["borrower_ref"] == "B-0001"


def test_tampering_repo_writes_nothing_and_forwards():
    inner = FakeRepo()
    repo = TamperingRepo(inner, Tamper(L_MINOR, "borrower_ref", "M", "M"))
    repo.get_loan(L_MINOR)
    assert repo.get_contacts(L_MINOR) == inner.get_contacts(L_MINOR)
    assert repo.list_requests() == [] and repo.get_request("ER-000001") is None
    assert (inner.drafts, inner.requests, inner.events) == ([], [], [])
    repo.log_event({"x": 1})
    assert inner.events == [{"x": 1}]


def test_tampering_repo_refuses_status_changes():
    repo = TamperingRepo(FakeRepo(), Tamper(L_MINOR, "borrower_ref", "M", "M"))
    with pytest.raises(PermissionError):
        repo.transition("ER-000001", "awaiting_raise", "approved", {})


# --- mixed retrieval ---------------------------------------------------------------------------


class FakeBase:
    def __init__(self, by_collection):
        self.by_collection = by_collection
        self.calls = []

    def __call__(self, query, mode, k, collection, include_superseded, query_vector, **kw):
        self.calls.append(
            {"collection": collection, "mode": mode, "k": k, "qv": query_vector, **kw}
        )
        return self.by_collection[collection][:k]


def test_mixed_retrieve_merges_orders_and_renumbers():
    base = FakeBase(
        {
            "main": [hit(1, "POL-001#a", 0.4000001), hit(2, "POL-001#b", 0.1)],
            "adversarial": [hit(1, "ADV-001#a", 0.4), hit(2, "ADV-001#b", 0.9)],
        }
    )
    fn = mixed_retrieve(base=base)
    out = fn("q", mode="rerank", k=3, query_vector=[1.0], client="c", dsn="d", rerank_n=7,
             reranker_url="u", rerank_timeout=2.0)  # fmt: skip
    assert [h.chunk_id for h in out] == ["ADV-001#b", "POL-001#a", "ADV-001#a"]
    assert [h.rank for h in out] == [1, 2, 3]
    assert [c["collection"] for c in base.calls] == ["main", "adversarial"]
    for c in base.calls:
        assert (c["mode"], c["k"], c["qv"], c["client"], c["dsn"]) == ("rerank", 3, [1.0], "c", "d")
        assert (c["rerank_n"], c["reranker_url"], c["rerank_timeout"]) == (7, "u", 2.0)


def test_mixed_retrieve_tie_breaks_by_chunk_id_within_a_collection():
    base = FakeBase({"main": [hit(1, "B#x", 0.5), hit(2, "A#x", 0.5)], "adversarial": []})
    assert [h.chunk_id for h in mixed_retrieve(base=base)("q", k=5)] == ["A#x", "B#x"]


def test_mixed_sections_merges_first_collection_wins():
    seen = []

    def base(chunk_ids, collection, *, store=None, dsn=None):
        seen.append((collection, list(chunk_ids)))
        known = {"main": ["POL-001#a", "X#dup"], "adversarial": ["ADV-001#a", "X#dup"]}[collection]
        return {c: SectionInfo(c, c.split("#")[0], collection, "s", False, "t")
                for c in chunk_ids if c in known}  # fmt: skip

    out = mixed_sections(base=base)(["POL-001#a", "ADV-001#a", "X#dup", "NONE#x"], dsn="d")
    assert set(out) == {"POL-001#a", "ADV-001#a", "X#dup"}
    assert out["X#dup"].title == "main"
    assert seen == [("main", ["POL-001#a", "ADV-001#a", "X#dup", "NONE#x"]),
                    ("adversarial", ["ADV-001#a", "NONE#x"])]  # fmt: skip


# --- spec copies -------------------------------------------------------------------------------


def test_description_append_copy(tmp_path, repo_files_unchanged):
    patch = ATS.by_id("C01").spec_patch
    specs_path, lock_path = write_spec_copy(patch, tmp_path)
    assert lock_path == specs.LOCK_PATH
    copied = {s["function"]["name"]: s for s in json.loads(specs_path.read_text())}
    assert copied["draft_reply"]["function"]["description"].endswith(patch.text)
    assert isinstance(config_for("on", patch), StartupFailure)
    assert config_for("on", patch).code == "tools_lock_mismatch"
    cfg = config_for("tool_pinning_off", patch)
    assert isinstance(cfg, boot.AgentConfig)
    desc = next(s for s in cfg.specs if s["function"]["name"] == "draft_reply")["function"]
    assert desc["description"].endswith(patch.text)


def test_drop_pattern_copy(tmp_path, repo_files_unchanged):
    specs_path, _ = write_spec_copy(ATS.by_id("C02").spec_patch, tmp_path)
    copied = {s["function"]["name"]: s for s in json.loads(specs_path.read_text())}
    props = copied["draft_reply"]["function"]["parameters"]["properties"]
    assert "pattern" not in props["recipient_contact_id"]
    assert "pattern" in props["loan_number"]


def test_add_tool_copy_fails_at_load(tmp_path, repo_files_unchanged):
    patch = ATS.by_id("C03").spec_patch
    specs_path, _ = write_spec_copy(patch, tmp_path)
    names = [s["function"]["name"] for s in json.loads(specs_path.read_text())]
    assert names[-1] == "submit_exception_request" and len(names) == 6
    for config in ("on", "tool_pinning_off", "all_off"):
        assert config_for(config, patch) == StartupFailure("specs_invalid")


def test_relock_copy_matches_its_lock(tmp_path, repo_files_unchanged):
    patch = ATS.by_id("C04").spec_patch
    specs_path, lock_path = write_spec_copy(patch, tmp_path)
    assert lock_path == tmp_path / "tools.lock"
    specs.check_lock(specs.load_specs(specs_path), lock_path)  # no ToolsLockMismatch
    cfg = config_for("on", specs_path=specs_path, lock_path=lock_path)
    assert isinstance(cfg, boot.AgentConfig)
    assert preflight_lock(lock_path, ATS.registered) == PreflightFailure("tools_lock")


def test_preflight_passes_on_the_repo_lock():
    assert preflight_lock(specs.LOCK_PATH, ATS.registered) is None


# --- switched configs --------------------------------------------------------------------------


def test_env_for_and_disabled_for():
    assert env_for("on") == {}
    assert env_for("scope_check_off") == {
        "AGENT_DISABLE": "scope_check",
        "AGENT_DEFENSES_OFF_ACK": "1",
    }
    assert len(disabled_for("all_off")) == 7
    with pytest.raises(ValueError):
        disabled_for("everything_off")


def test_config_for_never_touches_os_environ(monkeypatch):
    monkeypatch.delenv("AGENT_DISABLE", raising=False)
    monkeypatch.delenv("AGENT_DEFENSES_OFF_ACK", raising=False)
    before = dict(os.environ)
    on = config_for("on")
    one = config_for("scope_check_off")
    off = config_for("all_off")
    assert dict(os.environ) == before
    assert all(on.policy.defenses.values())
    assert [k for k, v in one.policy.defenses.items() if not v] == ["scope_check"]
    assert not any(off.policy.defenses.values())


def test_config_for_ignores_a_hostile_environment(monkeypatch):
    monkeypatch.setenv("AGENT_DISABLE", "approval_gate")
    monkeypatch.setenv("AGENT_DEFENSES_OFF_ACK", "1")
    assert all(config_for("on").policy.defenses.values())


def test_offered_tools_mirror_the_loop():
    assert "prepare_exception_request" not in offered_tools(config_for("on"), "broker")
    assert offered_tools(config_for("role_allowlist_off"), "broker") == frozenset(specs.TOOL_NAMES)


def test_unknown_patch_kind(tmp_path):
    with pytest.raises(ValueError):
        write_spec_copy(SpecPatch("rename", "draft_reply"), tmp_path)
