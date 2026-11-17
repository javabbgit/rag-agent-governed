"""The single entry point for model calls (chat + embeddings).

Only this module constructs openai.OpenAI clients. Fail closed: every openai.OpenAIError is
translated to the taxonomy in errors.py (our own LLMError passes through); any other exception
is a bug and propagates unchanged. There is no fallback to any other endpoint. The SDK does not
retry by default (LLM_MAX_RETRIES=0); a positive value re-enables SDK retries for chat() and
embed() on transient failures only (connection, 408/409/429/5xx). chat_tools() never retries.
Chat requests use LLM_TIMEOUT_S; embedding requests use the embed timeout (EMBED_TIMEOUT_S).
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
    call_id: str | None = None  # x-litellm-call-id
    cost_usd: float | None = None  # x-litellm-response-cost
    attempted_retries: int | None = None  # x-litellm-attempted-retries (gateway side)


@dataclass
class ToolChatResult:
    message: dict[str, Any]  # the assistant message as returned, for a verbatim echo
    finish_reason: str | None
    model: str
    resolved_model: str | None
    usage: dict[str, Any] | None
    latency_ms: float
    call_id: str | None = None
    cost_usd: float | None = None


@dataclass
class EmbedResult:
    vectors: list[list[float]]
    model: str
    latency_ms: float


class EmbeddingDimensionError(LLMError):
    """A returned vector does not have EMBED_DIM entries."""


def _header(headers: Any, name: str) -> str | None:
    """A response header value; absent or the literal "none" is None."""
    value = headers.get(name)
    if value is None or value.strip().lower() in ("", "none"):
        return None
    return value.strip()


def _number(value: str | None, kind: type = float) -> Any:
    try:
        return None if value is None else kind(value)
    except ValueError:
        return None


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
        common: dict[str, Any] = {"max_retries": settings.llm_max_retries}
        if http_client is not None:
            common["http_client"] = http_client
        self._chat = OpenAI(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            timeout=settings.llm_timeout_s,
            **common,
        )
        self._embed = OpenAI(
            base_url=settings.effective_embed_base_url,
            api_key=settings.effective_embed_api_key,
            timeout=settings.effective_embed_timeout_s,
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
            err = classify_http_error(exc.status_code, message, err_type, secrets=self._secrets)
            headers = exc.response.headers
            err.call_id = _header(headers, "x-litellm-call-id")
            err.retry_after = _number(_header(headers, "retry-after"))
            err.attempted_retries = _number(_header(headers, "x-litellm-attempted-retries"), int)
            return err
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
            call_id=_header(headers, "x-litellm-call-id"),
            cost_usd=_number(_header(headers, "x-litellm-response-cost")),
            attempted_retries=_number(_header(headers, "x-litellm-attempted-retries"), int),
        )

    def chat_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], **kw: Any
    ) -> ToolChatResult:
        """One tool-calling chat request for the agent loop. Never retried by the SDK."""
        start = time.perf_counter()
        try:
            raw = self._chat.with_options(max_retries=0).chat.completions.with_raw_response.create(
                model=self.settings.chat_model,
                messages=messages,  # type: ignore[arg-type]
                tools=tools,  # type: ignore[arg-type]
                **kw,
            )
            response = raw.parse()
        except (openai.OpenAIError, LLMError) as exc:
            raise self._translate(exc) from None
        latency_ms = (time.perf_counter() - start) * 1000
        if not response.choices:
            raise LLMError("chat response has no choices")
        headers = raw.headers
        resolved = headers.get("x-litellm-model-name")
        if resolved is None and self.settings.llm_mode == "direct":
            resolved = response.model
        choice = response.choices[0]
        return ToolChatResult(
            message=choice.message.model_dump(exclude_unset=True),
            finish_reason=choice.finish_reason,
            model=response.model,
            resolved_model=resolved,
            usage=response.usage.model_dump() if response.usage else None,
            latency_ms=latency_ms,
            call_id=_header(headers, "x-litellm-call-id"),
            cost_usd=_number(_header(headers, "x-litellm-response-cost")),
        )

    def embed(self, texts: list[str], timeout: float | None = None) -> EmbedResult:
        """Embeddings; `timeout` (seconds) overrides the embed timeout for this call only."""
        start = time.perf_counter()
        api = self._embed if timeout is None else self._embed.with_options(timeout=timeout)
        try:
            response = api.embeddings.create(
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
