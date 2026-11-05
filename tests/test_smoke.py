"""Unit tests for scripts/smoke.py: no network, LLMClient and DB are faked."""

import json

import pytest

import smoke
from rag_agent.errors import (
    GatewayUnavailableError,
    PolicyBlockedError,
    RequestRejectedError,
    UpstreamTimeoutError,
)
from rag_agent.llm_client import ChatResult, EmbedResult

FAKE_KEY = "sk-fakekey-ZZZ999-notreal"
BLOCK_MSG = (
    "policy_blocked: rule R10 — reroute target not allowed. Request contains data that may "
    "not be sent to chat-cloud (level high, reason detected)."
)


def chat_result(content="PONG", governance=None, resolved="real-model"):
    return ChatResult(
        content=content,
        model="chat-cloud",
        resolved_model=resolved,
        usage={"total_tokens": 2},
        latency_ms=1.0,
        governance=governance or {},
    )


class FakeClient:
    def __init__(self, chat=None, dim=4):
        self._chat = chat if chat is not None else chat_result()
        self.dim = dim
        self.chat_calls = 0
        self.temperatures = []

    def chat(self, messages, temperature=None, **kw):
        self.temperatures.append(temperature)
        self.chat_calls += 1
        if isinstance(self._chat, Exception):
            raise self._chat
        return self._chat

    def embed(self, texts):
        return EmbedResult(vectors=[[0.25] * self.dim for _ in texts], model="e", latency_ms=1.0)

    def list_models(self):
        return ["chat-cloud"]


# --- pii check ------------------------------------------------------------------------------


def test_pii_blocked_passes(settings):
    client = FakeClient(chat=PolicyBlockedError(BLOCK_MSG, 403))
    res = smoke.check_pii(client, settings)
    assert res["status"] == smoke.PASS
    assert res["detail"] == "blocked"
    assert (res["rule"], res["level"], res["reason"]) == ("R10", "high", "detected")


def test_pii_rerouted_passes(settings):
    gov = {"x-governance-routed-model": "chat-local", "x-governance-reason": "detected"}
    res = smoke.check_pii(FakeClient(chat=chat_result(governance=gov)), settings)
    assert res["status"] == smoke.PASS
    assert res["detail"] == "rerouted to chat-local"
    assert res["x_governance_reason"] == "detected"


def test_pii_answered_by_cloud_fails(settings):
    gov = {"x-governance-routed-model": "chat-cloud"}
    assert smoke.check_pii(FakeClient(chat=chat_result(governance=gov)), settings)["status"] == (
        smoke.FAIL
    )
    # no governance header at all is also a FAIL
    assert smoke.check_pii(FakeClient(), settings)["status"] == smoke.FAIL


def test_pii_reply_never_in_result(settings):
    res = smoke.check_pii(FakeClient(chat=chat_result(content="SECRET-REPLY-TEXT")), settings)
    assert "SECRET-REPLY-TEXT" not in json.dumps(res)


@pytest.mark.parametrize(
    "exc", [GatewayUnavailableError("down"), UpstreamTimeoutError("slow", 408)]
)
def test_pii_other_llm_error_fails(settings, exc):
    res = smoke.guarded(lambda: smoke.check_pii(FakeClient(chat=exc), settings), settings)
    assert res["status"] == smoke.FAIL


def test_guarded_sanitizes_keys_in_unexpected_exception(settings):
    def boom():
        raise RuntimeError(f"connection failed with key {settings.openai_api_key}")

    res = smoke.guarded(boom, settings)
    assert res["status"] == smoke.FAIL
    assert "RuntimeError" in res["detail"]
    assert settings.openai_api_key not in res["detail"]
    assert settings.effective_embed_api_key not in res["detail"]


def test_guarded_sanitizes_embed_key_and_extra_secret(settings):
    settings = settings.model_copy(update={"embed_api_key": "embed-secret-QQQ"})
    assert settings.effective_embed_api_key == "embed-secret-QQQ"

    def boom():
        raise ValueError("embed-secret-QQQ and extra-secret-RRR")

    res = smoke.guarded(boom, settings, "extra-secret-RRR")
    assert res["status"] == smoke.FAIL
    assert "embed-secret-QQQ" not in res["detail"]
    assert "extra-secret-RRR" not in res["detail"]


def test_pii_skipped_in_direct_mode(settings, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(smoke, "LLMClient", lambda s: client)
    direct = settings.model_copy(update={"llm_mode": "direct"})
    checks = smoke.run_checks(direct, "pii", pii=True)
    assert checks["pii"]["status"] == smoke.SKIPPED
    assert client.chat_calls == 0


def test_pii_skipped_without_flag(settings, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(smoke, "LLMClient", lambda s: client)
    checks = smoke.run_checks(settings, "pii", pii=False)
    assert checks["pii"]["status"] == smoke.SKIPPED
    assert client.chat_calls == 0


# --- report / exit code ---------------------------------------------------------------------


@pytest.fixture
def smoke_env(tmp_path, monkeypatch):
    for name in ("LLM_MODE", "OPENAI_BASE_URL", "OPENAI_API_KEY", "CHAT_MODEL", "EMBED_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(smoke, "REPO_ROOT", tmp_path)
    env = tmp_path / "test.env"
    env.write_text(
        "LLM_MODE=gateway\n"
        "OPENAI_BASE_URL=http://gateway.invalid:4000/v1\n"
        f"OPENAI_API_KEY={FAKE_KEY}\n"
        "CHAT_MODEL=chat-cloud\n"
        "EMBED_MODEL=rag-embed\n"
        "EMBED_DIM=4\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        smoke, "check_db", lambda s, state: smoke.result(smoke.PASS, None, "faked db")
    )
    return env


def _report(tmp_path):
    files = list((tmp_path / "reports" / "smoke").glob("*_gateway.json"))
    assert len(files) == 1
    return files[0].read_text(encoding="utf-8")


def test_report_has_no_key_and_correct_counts(smoke_env, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(smoke, "LLMClient", lambda s: FakeClient())
    code = smoke.main(["--env-file", str(smoke_env), "--pii"])
    out = capsys.readouterr().out
    text = _report(tmp_path)
    report = json.loads(text)

    assert code == 1, out  # the pii check fails by design here
    assert FAKE_KEY not in text
    assert FAKE_KEY not in out
    assert report["mode"] == "gateway"
    assert report["chat_model"] == "chat-cloud"
    assert report["temperature"] is None
    # config, models, chat, embed_plain, embed_loan_id, db pass; pii FAILS (answered by cloud)
    assert set(report["checks"]) == {
        "config",
        "models",
        "chat",
        "embed_plain",
        "embed_loan_id",
        "pii",
        "db",
    }
    assert report["counts"] == {"pass": 6, "fail": 1, "skipped": 0, "timeouts": 0}


def test_exit_code_1_on_fail_and_0_when_clean(smoke_env, tmp_path, monkeypatch):
    monkeypatch.setattr(smoke, "LLMClient", lambda s: FakeClient(chat=chat_result("nope")))
    assert smoke.main(["--env-file", str(smoke_env), "--only", "chat"]) == 1
    report = json.loads(_report(tmp_path))
    assert report["counts"] == {"pass": 1, "fail": 1, "skipped": 0, "timeouts": 0}  # config + chat


def test_exit_code_0_all_pass_and_skipped_pii(smoke_env, tmp_path, monkeypatch):
    monkeypatch.setattr(smoke, "LLMClient", lambda s: FakeClient())
    assert smoke.main(["--env-file", str(smoke_env)]) == 0
    report = json.loads(_report(tmp_path))
    assert report["checks"]["pii"]["status"] == smoke.SKIPPED
    assert report["counts"]["fail"] == 0
    assert report["counts"]["skipped"] == 1


def test_timeouts_are_counted(smoke_env, tmp_path, monkeypatch):
    monkeypatch.setattr(
        smoke, "LLMClient", lambda s: FakeClient(chat=UpstreamTimeoutError("slow", 408))
    )
    assert smoke.main(["--env-file", str(smoke_env), "--only", "chat"]) == 1
    assert json.loads(_report(tmp_path))["counts"]["timeouts"] == 1


def test_no_report_when_config_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(smoke, "REPO_ROOT", tmp_path)
    env = tmp_path / "bad.env"
    env.write_text("LLM_MODE=cloud\n", encoding="utf-8")
    for name in ("LLM_MODE", "OPENAI_BASE_URL", "OPENAI_API_KEY", "CHAT_MODEL", "EMBED_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert smoke.main(["--env-file", str(env)]) == 1
    assert not (tmp_path / "reports").exists()


# --- vector hash ----------------------------------------------------------------------------


def test_vector_sha256_stable_beyond_6_decimals():
    a = [0.1234561, 0.5, -0.25]
    b = [0.1234564, 0.5000000001, -0.2500000002]
    assert smoke.vector_sha256(a) == smoke.vector_sha256(b)
    assert smoke.vector_sha256(a) != smoke.vector_sha256([0.123457, 0.5, -0.25])


# --- models check / temperature -------------------------------------------------------------


class RejectingModelsClient(FakeClient):
    def list_models(self):
        raise RequestRejectedError("missing anthropic-version header", 400)


def test_models_rejected_skipped_in_direct_mode(settings):
    direct = settings.model_copy(update={"llm_mode": "direct"})
    res = smoke.check_models(RejectingModelsClient(), direct)
    assert res["status"] == smoke.SKIPPED
    assert res["detail"] == "model listing not supported by this endpoint"


def test_models_rejected_stays_strict_in_gateway_mode(settings):
    assert settings.llm_mode == "gateway"
    with pytest.raises(RequestRejectedError):
        smoke.check_models(RejectingModelsClient(), settings)
    res = smoke.guarded(lambda: smoke.check_models(RejectingModelsClient(), settings), settings)
    assert res["status"] == smoke.FAIL


def test_smoke_chat_checks_send_no_temperature(settings):
    client = FakeClient()
    smoke.check_chat(client, settings)
    smoke.check_pii(client, settings)
    assert client.temperatures == [None, None]
