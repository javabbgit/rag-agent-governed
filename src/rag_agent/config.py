"""Configuration, read from the environment (or an explicit env file)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """Raised at startup when configuration is missing or invalid."""


def mask_secret(value: str | None) -> str:
    """First 4 characters followed by an ellipsis; never the full key."""
    if not value:
        return "(unset)"
    return value[:4] + "…"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    llm_mode: Literal["gateway", "direct"] = "gateway"
    openai_base_url: str
    openai_api_key: str
    chat_model: str
    embed_model: str
    embed_base_url: str | None = None
    embed_api_key: str | None = None
    embed_dim: int = 768
    llm_timeout_s: float = 180.0
    llm_max_retries: int = 1
    database_url: str = "postgresql://rag:rag@127.0.0.1:5433/rag"
    reranker_url: str = "http://127.0.0.1:8081"
    reranker_model_rev: str = "2cfc18c9415c912f9d8155881c133215df768a70"
    rerank_timeout_s: float = 60.0
    answer_prompt: str = "src/rag_agent/prompts/answer_v1.md"
    answer_context_k: int = 5
    answer_max_tokens: int = 512
    question_max_chars: int = 1000
    answer_log_dir: str = "reports/answers"
    embed_timeout_s: float | None = None  # default: llm_timeout_s

    @property
    def effective_embed_timeout_s(self) -> float:
        return self.embed_timeout_s if self.embed_timeout_s is not None else self.llm_timeout_s

    @property
    def effective_embed_base_url(self) -> str:
        return self.embed_base_url or self.openai_base_url

    @property
    def effective_embed_api_key(self) -> str:
        return self.embed_api_key or self.openai_api_key

    def masked_summary(self) -> dict[str, object]:
        return {
            "llm_mode": self.llm_mode,
            "openai_base_url": self.openai_base_url,
            "openai_api_key": mask_secret(self.openai_api_key),
            "chat_model": self.chat_model,
            "embed_model": self.embed_model,
            "embed_base_url": self.effective_embed_base_url,
            "embed_api_key": mask_secret(self.effective_embed_api_key),
            "embed_dim": self.embed_dim,
            "llm_timeout_s": self.llm_timeout_s,
            "llm_max_retries": self.llm_max_retries,
            "database_url": _mask_db_url(self.database_url),
        }


def _mask_db_url(url: str) -> str:
    """Hide the password in a DSN (user:pass@host)."""
    if "://" not in url or "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.rsplit("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:…@{host}"


def load_settings(env_file: str | Path | None = None) -> Settings:
    """Load settings; raise ConfigError with a clear message on failure.

    With env_file=None, only the process environment is used plus a `.env` in the
    working directory if present.
    """
    if env_file is not None and not Path(env_file).is_file():
        raise ConfigError(f"env file not found: {env_file}")
    try:
        return Settings(_env_file=env_file if env_file is not None else ".env")  # type: ignore[call-arg]
    except ValidationError as exc:
        problems = []
        for err in exc.errors():
            name = ".".join(str(p) for p in err["loc"]).upper()
            if err["type"] == "missing":
                problems.append(f"{name}: required but not set")
            else:
                problems.append(f"{name}: {err['msg']}")
        raise ConfigError("invalid configuration — " + "; ".join(problems)) from None
