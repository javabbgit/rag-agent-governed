"""The single entry point for model calls (chat + embeddings).

Only this module constructs openai.OpenAI clients. Fail closed: every openai.OpenAIError is
translated to the taxonomy in errors.py (our own LLMError passes through); any other exception
is a bug and propagates unchanged. There is no fallback to any other endpoint, and the SDK's
retries only apply to transient failures (connection, 408/409/429/5xx).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx2
import openai
from openai import OpenAI

from rag_agent.config import Settings
from rag_agent.errors import (
    GatewayUnavailableError,
    LLMError,
    UpstreamTimeoutError,
    classify_http_error,
    sanitize,
)


@dataclass
class ChatResult:
    content: str
    model: str  # response.model (in gateway mode just the alias)
    resolved_model: str | None  # x-litellm-model-name header, else response.model in direct mode
    usage: dict[str, Any] | None
    latency_ms: float
    governance: dict[str, str] = field(default_factory=dict)  # x-governance-* headers


@dataclass
class EmbedResult:
    vectors: list[list[float]]
    model: str
    latency_ms: float


class EmbeddingDimensionError(LLMError):
    """A returned vector does not have EMBED_DIM entries."""


def _extract_error(exc: openai.APIStatusError) -> tuple[str, str | None]:
    """Return (message, error type) from the gateway's JSON error body."""
    body = exc.body
    if isinstance(body, dict):
        inner = body.get("error", body)
        if isinstance(inner, dict):
            return str(inner.get("message") or exc.message), inner.get("type")
    return exc.message, None


class LLMClient:
    def __init__(self, settings: Settings, http_client: httpx2.Client | None = None):
        self.settings = settings
        self._secrets = (settings.openai_api_key, settings.effective_embed_api_key)
        common: dict[str, Any] = {
            "timeout": settings.llm_timeout_s,
            "max_retries": settings.llm_max_retries,
        }
        if http_client is not None:
            common["http_client"] = http_client
        self._chat = OpenAI(
            base_url=settings.openai_base_url, api_key=settings.openai_api_key, **common
        )
        self._embed = OpenAI(
            base_url=settings.effective_embed_base_url,
            api_key=settings.effective_embed_api_key,
            **common,
        )

    # -- error translation -------------------------------------------------------------------

    def _translate(self, exc: openai.OpenAIError | LLMError) -> LLMError:
        if isinstance(exc, LLMError):
            return exc
        if isinstance(exc, openai.APITimeoutError):
            return UpstreamTimeoutError("request timed out", None)
        if isinstance(exc, openai.APIConnectionError):
            where = "gateway" if self.settings.llm_mode == "gateway" else "endpoint"
            return GatewayUnavailableError(f"{where} unreachable (connection failed)")
        if isinstance(exc, openai.APIStatusError):
            message, err_type = _extract_error(exc)
            return classify_http_error(exc.status_code, message, err_type, secrets=self._secrets)
        return LLMError(sanitize(f"unexpected {type(exc).__name__}", *self._secrets))

    # -- public API --------------------------------------------------------------------------

    def chat(
        self, messages: list[dict[str, Any]], temperature: float | None = None, **kw: Any
    ) -> ChatResult:
        """Chat completion. temperature=None omits the parameter (some models reject it)."""
        if temperature is not None:
            kw["temperature"] = temperature
        start = time.perf_counter()
        try:
            raw = self._chat.chat.completions.with_raw_response.create(
                model=self.settings.chat_model,
                messages=messages,  # type: ignore[arg-type]
                **kw,
            )
            response = raw.parse()
        except (openai.OpenAIError, LLMError) as exc:
            raise self._translate(exc) from None
        latency_ms = (time.perf_counter() - start) * 1000
        headers = raw.headers
        governance = {
            k.lower(): v for k, v in headers.items() if k.lower().startswith("x-governance-")
        }
        resolved = headers.get("x-litellm-model-name")
        if resolved is None and self.settings.llm_mode == "direct":
            resolved = response.model
        content = response.choices[0].message.content if response.choices else None
        return ChatResult(
            content=content or "",
            model=response.model,
            resolved_model=resolved,
            usage=response.usage.model_dump() if response.usage else None,
            latency_ms=latency_ms,
            governance=governance,
        )

    def embed(self, texts: list[str]) -> EmbedResult:
        start = time.perf_counter()
        try:
            response = self._embed.embeddings.create(
                model=self.settings.embed_model, input=texts, encoding_format="float"
            )
        except (openai.OpenAIError, LLMError) as exc:
            raise self._translate(exc) from None
        latency_ms = (time.perf_counter() - start) * 1000
        vectors = [list(item.embedding) for item in response.data]
        if len(vectors) != len(texts):
            raise EmbeddingDimensionError(f"expected {len(texts)} vectors, got {len(vectors)}")
        for vec in vectors:
            if len(vec) != self.settings.embed_dim:
                raise EmbeddingDimensionError(
                    f"embedding length {len(vec)} != EMBED_DIM {self.settings.embed_dim}"
                )
        return EmbedResult(vectors=vectors, model=response.model, latency_ms=latency_ms)

    def list_models(self) -> list[str]:
        """GET /models on the chat endpoint. Raises a taxonomy error (404/405 = unsupported)."""
        try:
            return [m.id for m in self._chat.models.list()]
        except (openai.OpenAIError, LLMError) as exc:
            raise self._translate(exc) from None
