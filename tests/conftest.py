import pytest

from rag_agent.config import Settings


@pytest.fixture(autouse=True)
def isolated_answer_log(tmp_path, monkeypatch):
    """Answer logs from any test (Settings and load_settings read the env) go to tmp_path."""
    monkeypatch.setenv("ANSWER_LOG_DIR", str(tmp_path / "answer_logs"))
    monkeypatch.setenv("AGENT_LOG_DIR", str(tmp_path / "agent_logs"))


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_mode="gateway",
        openai_base_url="http://gateway.invalid:4000/v1",
        openai_api_key="sk-test-key-1234",
        chat_model="chat-cloud",
        embed_model="rag-embed",
        embed_dim=4,
        llm_max_retries=0,
    )
