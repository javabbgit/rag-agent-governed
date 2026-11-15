"""Tool pinning: tools.lock against the specs."""

import json
from pathlib import Path

import pytest

from rag_agent.agent import specs as specs_mod
from rag_agent.agent.boot import load_agent_config
from rag_agent.agent.errors import ToolsLockMismatch


@pytest.fixture
def pinned(tmp_path):
    """A copy of the specs with a lock generated from them."""
    specs_path = tmp_path / "tool_specs.json"
    specs_path.write_bytes(specs_mod.SPECS_PATH.read_bytes())
    lock_path = tmp_path / "tools.lock"
    lock_path.write_text(specs_mod.render_lock(specs_mod.load_specs(specs_path)), encoding="utf-8")
    return specs_path, lock_path


def _edit(specs_path: Path, fn):
    specs = json.loads(specs_path.read_text(encoding="utf-8"))
    fn(specs)
    specs_path.write_text(json.dumps(specs), encoding="utf-8")


def test_matching_lock_passes(pinned):
    specs_path, lock_path = pinned
    specs_mod.check_lock(specs_mod.load_specs(specs_path), lock_path)


def test_hash_follows_the_spec_formula():
    spec = specs_mod.load_specs()[0]
    import hashlib

    text = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert specs_mod.tool_hash(spec) == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_changed_tool_is_named(pinned):
    specs_path, lock_path = pinned
    _edit(specs_path, lambda s: s[1]["function"].update(description="Returns a loan record."))
    with pytest.raises(ToolsLockMismatch) as err:
        specs_mod.check_lock(specs_mod.load_specs(specs_path), lock_path)
    assert err.value.changed == ["get_loan_status"] and "get_loan_status" in str(err.value)


def test_missing_and_extra_tools_are_named(pinned):
    specs_path, lock_path = pinned
    specs = specs_mod.load_specs(specs_path)
    lock_path.write_text(
        specs_mod.render_lock(specs).replace("draft_reply", "old_tool"), encoding="utf-8"
    )
    with pytest.raises(ToolsLockMismatch) as err:
        specs_mod.check_lock(specs, lock_path)
    assert err.value.missing == ["old_tool"] and err.value.extra == ["draft_reply"]


def test_absent_lock_is_a_mismatch(tmp_path):
    with pytest.raises(ToolsLockMismatch):
        specs_mod.check_lock(specs_mod.load_specs(), tmp_path / "nope.lock")


def test_p10_one_character_change_stops_startup_before_any_client_call(pinned):
    specs_path, lock_path = pinned

    def tweak(specs):
        fn = specs[0]["function"]
        fn["description"] = fn["description"][:-1] + "!"

    _edit(specs_path, tweak)
    client_calls = []  # nothing here builds or calls a client
    with pytest.raises(ToolsLockMismatch):
        load_agent_config(specs_path=specs_path, lock_path=lock_path, env={})
    assert client_calls == []


def test_pinning_off_warns_once_and_continues(pinned, capsys):
    specs_path, lock_path = pinned
    _edit(specs_path, lambda s: s[0]["function"].update(description="Changed."))
    env = {"AGENT_DISABLE": "tool_pinning", "AGENT_DEFENSES_OFF_ACK": "1"}
    cfg = load_agent_config(specs_path=specs_path, lock_path=lock_path, env=env)
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and err[0].startswith("warning:") and "search_policy" in err[0]
    assert len(cfg.specs) == 5


def test_tools_for_role_follows_the_allow_list(pinned):
    specs_path, lock_path = pinned
    cfg = load_agent_config(specs_path=specs_path, lock_path=lock_path, env={})
    names = lambda role: {s["function"]["name"] for s in cfg.tools_for_role(role)}  # noqa: E731
    assert "prepare_exception_request" not in names("broker")
    assert "prepare_exception_request" not in names("senior_underwriter")
    assert "prepare_exception_request" in names("underwriter")


def test_repo_lock_matches_the_specs():
    # Fails until the human runs `make tools-lock` (no tools.lock is committed by D7-A).
    specs_mod.check_lock(specs_mod.load_specs())
