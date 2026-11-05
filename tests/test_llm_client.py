import json

import httpx2
import pytest

from fixtures import load_fixture
from rag_agent.errors import (
    GatewayAuthError,
    GatewayUnavailableError,
    PolicyBlockedError,
    UpstreamTimeoutError,
)
from rag_agent.llm_client import EmbeddingDimensionError, LLMClient


def make_client(settings, handler):
    seen: list[httpx2.Request] = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    http = httpx2.Client(transport=httpx2.MockTransport(wrapped))
    return LLMClient(settings, http_client=http), seen


def chat_ok(request):
    return httpx2.Response(
        200,
        headers={"x-litellm-model-name": "real-upstream", "x-governance-routed-model": "m"},
        json={
            "id": "c1",
            "object": "chat.completion",
            "created": 0,
            "model": "chat-cloud",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "PONG"},
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )


def test_chat_success_headers(settings):
    client, seen = make_client(settings, chat_ok)
    res = client.chat([{"role": "user", "content": "hi"}])
    assert res.content == "PONG"
    assert res.model == "chat-cloud"
    assert res.resolved_model == "real-upstream"
    assert res.governance == {"x-governance-routed-model": "m"}
    assert res.usage["total_tokens"] == 2
    assert "temperature" not in json.loads(seen[0].content)


def test_chat_sends_temperature_when_given(settings):
    client, seen = make_client(settings, chat_ok)
    client.chat([{"role": "user", "content": "hi"}], temperature=0)
    assert json.loads(seen[0].content)["temperature"] == 0


def test_connection_error_fails_closed(settings):
    def refuse(request):
        raise httpx2.ConnectError("refused")

    client, seen = make_client(settings, refuse)
    with pytest.raises(GatewayUnavailableError):
        client.chat([{"role": "user", "content": "hi"}])
    assert {r.url.host for r in seen} == {"gateway.invalid"}  # nothing sent elsewhere


def test_connection_error_not_retried_elsewhere_with_retries(settings):
    settings.llm_max_retries = 2

    def refuse(request):
        raise httpx2.ConnectError("refused")

    client, seen = make_client(settings, refuse)
    with pytest.raises(GatewayUnavailableError):
        client.embed(["x"])
    assert {r.url.host for r in seen} == {"gateway.invalid"}


def test_timeout(settings):
    def slow(request):
        raise httpx2.ReadTimeout("slow")

    client, _ = make_client(settings, slow)
    with pytest.raises(UpstreamTimeoutError):
        client.chat([{"role": "user", "content": "hi"}])


@pytest.mark.parametrize(
    ("name", "cls"), [("pii", PolicyBlockedError), ("bad_key", GatewayAuthError)]
)
def test_fixture_errors_not_retried(settings, name, cls):
    settings.llm_max_retries = 3
    status, body, _ = load_fixture(name)
    client, seen = make_client(
        settings,
        lambda r: httpx2.Response(
            status, content=body, headers={"content-type": "application/json"}
        ),
    )
    with pytest.raises(cls) as ei:
        client.chat([{"role": "user", "content": "hi"}])
    assert len(seen) == 1
    assert settings.openai_api_key not in ei.value.message


def embed_response(dim):
    def handler(request):
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "model": "rag-embed",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1] * dim}],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    return handler


def test_embed_ok_and_float_format(settings):
    client, seen = make_client(settings, embed_response(4))
    res = client.embed(["x"])
    assert len(res.vectors[0]) == 4
    assert json.loads(seen[0].content)["encoding_format"] == "float"


def test_embed_wrong_dimension(settings):
    client, _ = make_client(settings, embed_response(3))
    with pytest.raises(EmbeddingDimensionError):
        client.embed(["x"])


@pytest.mark.parametrize("call", ["chat", "embed", "list_models"])
def test_non_openai_exception_propagates_unchanged(settings, call):
    client, _ = make_client(settings, chat_ok)
    calls = {
        "chat": lambda: client.chat([{"role": "user", "content": "hi"}]),
        "embed": lambda: client.embed(["x"]),
        "list_models": client.list_models,
    }
    sentinel = TypeError("not an openai error")

    def raiser(*a, **k):
        raise sentinel

    client._chat.chat.completions.with_raw_response.create = raiser
    client._embed.embeddings.create = raiser
    client._chat.models.list = raiser
    with pytest.raises(TypeError) as ei:
        calls[call]()
    assert ei.value is sentinel
