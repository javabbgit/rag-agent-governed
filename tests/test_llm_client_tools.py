"""LLMClient.chat_tools against the recorded fixtures and a mock transport. No network."""

import httpx2
import pytest

from agent_loop_fakes import chat_bodies, recorded_message, replay_client
from rag_agent.errors import UpstreamServerError
from rag_agent.llm_client import LLMClient

TOOLS = [{"type": "function", "function": {"name": "t", "description": "d", "parameters": {}}}]
USER = [{"role": "user", "content": "hi"}]


def test_tool_call_fixture_gives_the_recorded_message(settings):
    client, seen = replay_client(settings, ("gateway_ok", "agent_tool_call"))
    res = client.chat_tools(USER, TOOLS, tool_choice="auto", max_tokens=64)
    assert res.message == recorded_message("agent_tool_call")
    assert res.message["tool_calls"][0]["caller"] == {"type": "direct"}
    assert res.finish_reason == "tool_calls"
    assert res.model == "chat-cloud"
    assert res.resolved_model == "anthropic/claude-haiku-5-5"
    assert res.call_id == "64d81bf6-d6bd-4d21-b6fd-bf012f88da1a"
    assert res.cost_usd == 8.63e-05
    assert res.usage["total_tokens"] == 639
    body = chat_bodies(seen)[0]
    assert body["tools"] == TOOLS and body["tool_choice"] == "auto" and body["max_tokens"] == 64
    assert not {"temperature", "top_p", "seed"} & set(body)


def test_final_fixture_has_no_tool_calls(settings):
    client, _ = replay_client(settings, ("gateway_ok", "agent_final"))
    res = client.chat_tools(USER, TOOLS)
    assert res.message == recorded_message("agent_final")
    assert "tool_calls" not in res.message and res.finish_reason == "stop"


def test_server_error_is_requested_once_whatever_the_retry_setting(settings):
    settings.llm_max_retries = 3
    seen = []

    def handler(request):
        seen.append(request)
        return httpx2.Response(500, json={"error": {"message": "boom", "type": "server_error"}})

    client = LLMClient(settings, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    with pytest.raises(UpstreamServerError):
        client.chat_tools(USER, TOOLS)
    assert len(seen) == 1
    assert {r.url.host for r in seen} == {"gateway.invalid"}
    assert client._chat.max_retries == 3  # chat() keeps the configured retries


def test_chat_is_unchanged_by_chat_tools(settings):
    client, seen = replay_client(
        settings, ("gateway_ok", "agent_tool_call"), ("gateway_ok", "chat_ok")
    )
    client.chat_tools(USER, TOOLS)
    res = client.chat(USER, max_tokens=5)
    assert res.resolved_model == "anthropic/claude-haiku-5-5"
    assert "tools" not in chat_bodies(seen)[1]
