"""Fakes for the agent loop tests: a scripted chat client and fixture replay. No network."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2

from answer_fakes import FakeRetriever, hits_from, make_runtime
from rag_agent.answer import Runtime
from rag_agent.config import Settings
from rag_agent.llm_client import LLMClient, ToolChatResult

FIXTURES = Path(__file__).parent / "fixtures"
CHUNK_1 = "DOC-001#s1"  # first chunk the fake retriever returns
EMBED_OK = {"data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3, 0.4]}]}


def tc(name: str, call_id: str = "t1", **args: Any) -> dict[str, Any]:
    """A tool call as it appears in an assistant message."""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _result(message: dict[str, Any], finish_reason: str) -> ToolChatResult:
    return ToolChatResult(
        message=message,
        finish_reason=finish_reason,
        model="chat-cloud",
        resolved_model="upstream/model",
        usage=None,
        latency_ms=1.0,
        call_id=None,
        cost_usd=0.001,
    )


def tool_reply(*calls: dict[str, Any]) -> ToolChatResult:
    message = {"role": "assistant", "content": None, "tool_calls": list(calls)}
    return _result(message, "tool_calls")


def final_reply(text: str, finish_reason: str = "stop") -> ToolChatResult:
    return _result({"role": "assistant", "content": text}, finish_reason)


class ScriptedClient:
    """Returns the queued replies in order; an exception in the queue is raised."""

    def __init__(self, *replies: Any):
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        self.embeds: list[list[str]] = []

    def chat_tools(self, messages: list[dict[str, Any]], tools: Any, **kw: Any) -> ToolChatResult:
        self.requests.append({"messages": copy.deepcopy(messages), "tools": tools, "kw": kw})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def embed(self, texts: list[str], timeout: float | None = None) -> Any:
        self.embeds.append(texts)
        return SimpleNamespace(vectors=[[0.1, 0.2, 0.3, 0.4]], model="m", latency_ms=1.0)


def runtime_for(settings: Settings, client: Any) -> Runtime:
    """A Runtime whose search path needs no network: fake retriever, two current chunks."""
    retriever = FakeRetriever(hits_from([(0.9, "current"), (0.8, "current")]))
    return make_runtime(settings, client, retriever)


def fixture_response(kind: str, name: str) -> httpx2.Response:
    """A recorded gateway reply: kind is gateway_ok or gateway_errors."""
    lines = (FIXTURES / kind / f"{name}.headers").read_text(encoding="utf-8").splitlines()
    status = int(lines[0].split()[1])
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
    for drop in ("content-length", "transfer-encoding", "content-encoding"):
        headers.pop(drop, None)
    body = (FIXTURES / kind / f"{name}.txt").read_text(encoding="utf-8").rstrip("\n")
    if kind == "gateway_errors":  # the last line is the HTTP status
        body = body.rpartition("\n")[0]
    return httpx2.Response(status, headers=headers, content=body.encode())


def recorded_message(name: str) -> dict[str, Any]:
    body = (FIXTURES / "gateway_ok" / f"{name}.txt").read_text(encoding="utf-8")
    return json.loads(body)["choices"][0]["message"]


def replay_client(settings: Settings, *chat: tuple[str, str]) -> tuple[LLMClient, list[Any]]:
    """An LLMClient on a mock transport that answers chat requests with the fixtures in order."""
    queue = list(chat)
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if request.url.path.endswith("/embeddings"):
            return httpx2.Response(200, json=EMBED_OK)
        return fixture_response(*queue.pop(0))

    http = httpx2.Client(transport=httpx2.MockTransport(handler))
    return LLMClient(settings, http_client=http), seen


def chat_bodies(seen: list[Any]) -> list[dict[str, Any]]:
    return [json.loads(r.content) for r in seen if r.url.path.endswith("/chat/completions")]
