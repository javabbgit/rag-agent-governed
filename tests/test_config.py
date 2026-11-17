import pytest

from rag_agent.config import ConfigError, load_settings, mask_secret

ENV_VARS = [
    "LLM_MODE",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "CHAT_MODEL",
    "EMBED_MODEL",
    "EMBED_BASE_URL",
    "EMBED_API_KEY",
    "EMBED_DIM",
    "LLM_TIMEOUT_S",
    "LLM_MAX_RETRIES",
    "DATABASE_URL",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # no stray .env


def _write_env(path, **values):
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return path


REQUIRED = {
    "OPENAI_BASE_URL": "http://localhost:4000/v1",
    "OPENAI_API_KEY": "sk-abcdef123456",
    "CHAT_MODEL": "chat-cloud",
    "EMBED_MODEL": "rag-embed",
}


def test_defaults_and_fallbacks(tmp_path):
    s = load_settings(_write_env(tmp_path / "e", **REQUIRED))
    assert s.llm_mode == "gateway"
    assert s.embed_dim == 768
    assert s.llm_timeout_s == 75
    assert s.llm_max_retries == 0
    assert s.embed_timeout_s == 10.0
    assert s.effective_embed_timeout_s == 10.0
    assert s.database_url == "postgresql://rag:rag@127.0.0.1:5433/rag"
    assert s.effective_embed_base_url == REQUIRED["OPENAI_BASE_URL"]
    assert s.effective_embed_api_key == REQUIRED["OPENAI_API_KEY"]


def test_embed_overrides(tmp_path):
    env = _write_env(
        tmp_path / "e", **REQUIRED, EMBED_BASE_URL="http://emb:1/v1", EMBED_API_KEY="other-key"
    )
    s = load_settings(env)
    assert s.effective_embed_base_url == "http://emb:1/v1"
    assert s.effective_embed_api_key == "other-key"


def test_masking(tmp_path):
    assert mask_secret("sk-abcdef123456") == "sk-a…"
    assert mask_secret("") == "(unset)"
    s = load_settings(_write_env(tmp_path / "e", **REQUIRED))
    summary = s.masked_summary()
    assert summary["openai_api_key"] == "sk-a…"
    assert "abcdef123456" not in str(summary)
    assert "rag:rag" not in str(summary)  # DB password hidden


def test_invalid_mode(tmp_path):
    with pytest.raises(ConfigError, match="LLM_MODE"):
        load_settings(_write_env(tmp_path / "e", **REQUIRED, LLM_MODE="cloud"))


def test_missing_required(tmp_path):
    with pytest.raises(ConfigError) as ei:
        load_settings(_write_env(tmp_path / "e", LLM_MODE="direct"))
    msg = str(ei.value)
    for name in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "CHAT_MODEL", "EMBED_MODEL"):
        assert name in msg


def test_env_file_missing(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "nope.env")


def test_process_env_is_used(monkeypatch):
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("EMBED_DIM", "16")
    assert load_settings().embed_dim == 16
