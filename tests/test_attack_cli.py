"""scripts/attack_eval.py: check, matrix, delivery, prescan with an injected Env. No network."""

import json
from collections import Counter
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import attack_eval
from agent_fakes import FakeRepo
from rag_agent.agent_attack.attacks import ATTACKS_PATH, load_attacks
from rag_agent.errors import PolicyBlockedError
from test_attack_matrix import fake_deps

TEXT = ATTACKS_PATH.read_text(encoding="utf-8")
ATS = load_attacks()


def copy_with(tmp_path, old, new):
    assert old in TEXT
    path = tmp_path / "attacks.toml"
    path.write_text(TEXT.replace(old, new, 1), encoding="utf-8")
    return str(path)


def make_env(settings, tmp_path, **kw):
    base = dict(
        load_settings=lambda: settings,
        dsn=lambda: "postgresql://unused",
        make_deps=lambda dsn: fake_deps(),
        repo_factory=lambda dsn: FakeRepo(),
        git_info=lambda: ("abc123", False),
        sleep=lambda s: None,
        clock=lambda: datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC),
        reports_dir=tmp_path / "reports",
    )
    base.update(kw)
    return attack_eval.Env(**base)


# --- check -------------------------------------------------------------------------------------


def test_check_prints_the_summary(capsys):
    assert attack_eval.main(["check"]) == 0
    assert capsys.readouterr().out == "attacks: 19 (live 18, matrix-only 1); mismatches: 0\n"


def test_check_registered_hash_mismatch(tmp_path, capsys):
    path = copy_with(tmp_path, 'tools_lock_sha256 = "befd', 'tools_lock_sha256 = "0efd')
    assert attack_eval.main(["check", "--attacks", path]) == 1
    out = capsys.readouterr().out
    assert "registered tools_lock_sha256 differs from the current file" in out
    assert out.endswith("mismatches: 1\n")


def test_check_file_error(tmp_path, capsys):
    path = copy_with(tmp_path, 'id = "A02"', 'id = "A01"')
    assert attack_eval.main(["check", "--attacks", path]) == 1
    assert "attack-check: attacks[A01]: duplicate id" in capsys.readouterr().err


# --- matrix ------------------------------------------------------------------------------------


def test_matrix_with_a_wrong_copy_fails(tmp_path, settings, capsys):
    path = copy_with(
        tmp_path,
        'default = { out = ["policy:arg_provenance"], hit = [] }',
        'default = { out = ["drafted"], hit = [] }',
    )
    env = make_env(settings, tmp_path)
    assert attack_eval.main(["matrix", "--attacks", path], env) == 1
    out = capsys.readouterr().out
    assert "A01 on: out ['policy:arg_provenance'] expected ['drafted']" in out
    # FakeRepo has no foreign keys, so C02 all_off cannot give db_fk here (the database does)
    assert "C02 all_off: out ['persisted'] expected ['db_fk']" in out
    assert "/171 as expected" in out
    assert "positives T4 T5: pass" in out
    reports = sorted(p.name for p in (tmp_path / "reports").iterdir())
    assert reports == ["attack_matrix_20261118T120000Z.json", "attack_matrix_20261118T120000Z.md"]
    report = json.loads((tmp_path / "reports" / reports[0]).read_text())
    assert report["total"] == 171 and report["git_commit"] == "abc123"


# --- prescan -----------------------------------------------------------------------------------


class RecordingClient:
    def __init__(self, fail_after=None):
        self.sent = []
        self.fail_after = fail_after

    def chat(self, messages, **kw):
        if self.fail_after is not None and len(self.sent) >= self.fail_after:
            raise RuntimeError("secret gateway text")
        self.sent.append((messages, kw))


def test_prescan_items():
    items = attack_eval.prescan_items(ATS, FakeRepo())
    kinds = Counter(kind for _, kind, _ in items)
    assert len(items) == 46
    assert kinds == {
        "question": 18,
        "chunk_enveloped": 10,
        "chunk_plain": 10,
        "tamper_enveloped": 3,
        "tamper_plain": 3,
        "spec_text": 2,
    }
    assert "C04" not in {a for a, _, _ in items}
    env = next(t for a, k, t in items if a == "A01" and k == "chunk_enveloped")
    chunk = json.loads(env)["data"]["chunks"][0]
    assert (chunk["marker"], chunk["score"]) == ("S1", 0.99)
    assert set(chunk) == {"marker", "chunk_id", "doc_id", "title", "section", "score", "text"}
    tamper = json.loads(next(t for a, k, t in items if a == "B01" and k == "tamper_enveloped"))
    assert tamper["tool"] == "get_loan_status" and "status" not in tamper["data"]
    assert "ZQX-B01" in tamper["data"]["outstanding_conditions"][-1]
    plain = next(t for a, k, t in items if a == "B03" and k == "tamper_plain")
    assert plain.startswith("status: ok\n") and "ZQX-B03" in plain


def test_prescan_sends_every_item(tmp_path, settings, capsys):
    client = RecordingClient()
    pauses = []
    env = make_env(settings, tmp_path, client_factory=lambda s: client, sleep=pauses.append)
    assert attack_eval.main(["prescan"], env) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "1 A01 question pass"
    assert lines[-1] == "requests sent: 46; blocked: 0"
    assert len(lines) == 47
    assert len(client.sent) == 46 and len(pauses) == 45
    assert {kw["max_tokens"] for _, kw in client.sent} == {16}


def test_prescan_stops_on_the_first_failure(tmp_path, settings, capsys):
    env = make_env(settings, tmp_path, client_factory=lambda s: RecordingClient(fail_after=2))
    assert attack_eval.main(["prescan"], env) == 1
    out = capsys.readouterr().out
    assert out.endswith("attack-prescan failed after 2 requests: RuntimeError\n")
    assert "secret" not in out


class BlockingClient:
    """Blocks item 3 at the gateway (rule R10) and fails on item 5 with any other error."""

    def __init__(self):
        self.n = 0

    def chat(self, messages, **kw):
        self.n += 1
        if self.n == 3:
            raise PolicyBlockedError("policy_blocked: rule R10", 403)
        if self.n == 5:
            raise RuntimeError("secret gateway text")


def test_prescan_block_is_a_result_and_other_errors_stop(tmp_path, settings, capsys):
    items = attack_eval.prescan_items(ATS, FakeRepo())
    env = make_env(settings, tmp_path, client_factory=lambda s: BlockingClient())
    assert attack_eval.main(["prescan"], env) == 1
    lines = capsys.readouterr().out.splitlines()
    expected = [f"{n} {a} {k}" for n, (a, k, _) in enumerate(items[:4], start=1)]
    assert lines == [
        f"{expected[0]} pass",
        f"{expected[1]} pass",
        f"{expected[2]} blocked rule=R10",  # the scan continues after a block
        f"{expected[3]} pass",
        "attack-prescan failed after 4 requests: RuntimeError",  # and stops on anything else
    ]


def test_prescan_completes_with_blocks(tmp_path, settings, capsys):
    class BlockOne(BlockingClient):
        def chat(self, messages, **kw):
            self.n += 1
            if self.n == 3:
                raise PolicyBlockedError("policy_blocked: rule R10", 403)

    env = make_env(settings, tmp_path, client_factory=lambda s: BlockOne())
    assert attack_eval.main(["prescan"], env) == 0
    out = capsys.readouterr().out.splitlines()
    assert sum(line.endswith("blocked rule=R10") for line in out) == 1
    assert out[-1] == "requests sent: 46; blocked: 1"


def test_prescan_gateway_mode_only(tmp_path, settings, capsys):
    direct = settings.model_copy(update={"llm_mode": "direct"})
    env = make_env(direct, tmp_path, client_factory=lambda s: pytest.fail("no client"))
    assert attack_eval.main(["prescan"], env) == 1
    assert "gateway mode only" in capsys.readouterr().err


# --- delivery ----------------------------------------------------------------------------------


def source(chunk_id):
    return SimpleNamespace(chunk_id=chunk_id, doc_id=chunk_id.split("#")[0])


def prep(*chunk_ids, terminal=None):
    return SimpleNamespace(context=[source(c) for c in chunk_ids], terminal=terminal)


INSTR = "ADV-001#routing-a-tds-exception-to-the-credit-committee"


def test_delivery_row_and_format():
    a01 = ATS.by_id("A01")
    row = attack_eval.delivery_row(a01, prep("POL-010#summary", INSTR, "ADV-001#summary"), INSTR)
    assert (row["rank"], row["gate"], row["instruction_in_bait"], row["delivered"]) == (
        2, True, True, True,
    )  # fmt: skip
    assert attack_eval.format_delivery(row) == (
        "A01 rank=2 gate=pass instruction_in_bait=yes delivered=yes"
    )
    row = attack_eval.delivery_row(a01, prep("ADV-001#summary", INSTR), INSTR)
    assert row["instruction_in_bait"] is False and row["delivered"]
    row = attack_eval.delivery_row(a01, prep("POL-010#summary"), INSTR)
    assert row["rank"] is None and not row["delivered"]
    gate = SimpleNamespace(status="fallback", reason="low_score")
    row = attack_eval.delivery_row(a01, prep(terminal=gate), INSTR)
    assert attack_eval.format_delivery(row) == (
        "A01 rank=absent gate=fail instruction_in_bait=no delivered=no (fallback/low_score)"
    )


def fake_runtime(known=True, problem=None):
    def sections_fn(chunk_ids, **kw):
        return {c: object() for c in chunk_ids} if known else {}

    return SimpleNamespace(sections_fn=sections_fn, reranker_check=lambda s: problem)


def test_delivery_all_delivered(tmp_path, settings, monkeypatch, capsys):
    def fake_prepare(question, rt, allow_degraded=True):
        assert allow_degraded is False
        attack = next(a for a in ATS.live() if a.question == question)
        chunk = next(s.chunk_id for s in attack_eval.parse_doc(
            attack_eval.doc_path(attack.doc)).sections if "summary" not in s.chunk_id)  # fmt: skip
        return prep(f"{attack.doc}#summary", chunk)

    monkeypatch.setattr(attack_eval.answer, "prepare", fake_prepare)
    env = make_env(settings, tmp_path, runtime_factory=lambda s: fake_runtime())
    assert attack_eval.main(["delivery"], env) == 0
    out = capsys.readouterr().out.splitlines()
    assert [line.split()[0] for line in out[:5]] == ["A01", "A02", "A03", "A04", "A05"]
    assert all("rank=2 gate=pass instruction_in_bait=no delivered=yes" in x for x in out[:5])
    assert out[-1] == "delivery: 5/5 delivered"


def test_delivery_needs_the_adversarial_collection(tmp_path, settings, monkeypatch, capsys):
    monkeypatch.setattr(attack_eval.answer, "prepare", lambda *a, **k: pytest.fail("no request"))
    env = make_env(settings, tmp_path, runtime_factory=lambda s: fake_runtime(known=False))
    assert attack_eval.main(["delivery"], env) == 1
    assert "make ingest COLLECTION=adversarial" in capsys.readouterr().err


def test_delivery_reranker_preflight(tmp_path, settings, monkeypatch, capsys):
    monkeypatch.setattr(attack_eval.answer, "prepare", lambda *a, **k: pytest.fail("no request"))
    env = make_env(settings, tmp_path, runtime_factory=lambda s: fake_runtime(problem="down"))
    assert attack_eval.main(["delivery"], env) == 1
    assert "reranker unavailable (down)" in capsys.readouterr().err


def test_delivery_fails_when_one_is_not_delivered(tmp_path, settings, monkeypatch, capsys):
    monkeypatch.setattr(attack_eval.answer, "prepare", lambda q, rt, **k: prep("POL-010#summary"))
    env = make_env(settings, tmp_path, runtime_factory=lambda s: fake_runtime())
    assert attack_eval.main(["delivery"], env) == 1
    assert capsys.readouterr().out.splitlines()[-1] == "delivery: 0/5 delivered"


# --- usage -------------------------------------------------------------------------------------


@pytest.mark.parametrize("cmd", ["check", "matrix", "delivery", "prescan"])
def test_usage(cmd, capsys):
    with pytest.raises(SystemExit) as info:
        attack_eval.main([cmd, "--help"])
    assert info.value.code == 0
    assert f"usage: attack_eval.py {cmd} [-h] [--attacks ATTACKS]" in capsys.readouterr().out
