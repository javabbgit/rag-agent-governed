"""Fakes for answer tests: no network, no database."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from rag_agent.answer import Runtime
from rag_agent.config import Settings
from rag_agent.llm_client import ChatResult
from rag_agent.retrieve import Hit, SectionInfo

THRESHOLD = 0.0621  # fake threshold for tests only
PROMPT = "You are a test prompt."


def hit(rank: int, chunk_id: str, score: float, status: str = "current") -> Hit:
    return Hit(
        chunk_id=chunk_id,
        doc_id=chunk_id.split("#")[0],
        heading=f"Heading {chunk_id}",
        status=status,
        text=f"Body of {chunk_id}.",
        rank=rank,
        score=score,
        bm25_rank=None,
        bm25_score=None,
        vector_rank=None,
        vector_score=None,
        rrf_rank=rank,
    )


def hits_from(spec: list[tuple[float, str]]) -> list[Hit]:
    """spec: (score, 'current'|'superseded') in rank order."""
    return [hit(i, f"DOC-{i:03d}#s{i}", s, st) for i, (s, st) in enumerate(spec, start=1)]


def chat_result(text: str, **kw: Any) -> ChatResult:
    return ChatResult(
        content=text,
        model="chat-cloud",
        resolved_model="upstream/model",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        latency_ms=1.0,
        call_id="call-1",
        cost_usd=0.5,
        **kw,
    )


class FakeClient:
    def __init__(self, chat: Any = None, embed_error: Exception | None = None):
        self.chat_reply = chat
        self.embed_error = embed_error
        self.embeds: list[Any] = []
        self.chats: list[Any] = []

    def embed(self, texts: list[str], timeout: float | None = None) -> Any:
        self.embeds.append((texts, timeout))
        if self.embed_error:
            raise self.embed_error
        return SimpleNamespace(vectors=[[0.1, 0.2, 0.3, 0.4]], model="m", latency_ms=1.0)

    def chat(self, messages: list[dict[str, Any]], **kw: Any) -> ChatResult:
        self.chats.append((messages, kw))
        reply = self.chat_reply
        if isinstance(reply, Exception):
            raise reply
        return chat_result(reply if isinstance(reply, str) else "")


class FakeRetriever:
    def __init__(self, hits: list[Hit], rerank_error: Exception | None = None):
        self.hits = hits
        self.rerank_error = rerank_error
        self.calls: list[dict[str, Any]] = []

    def __call__(self, query: str, mode: str = "hybrid", k: int = 10, **kw: Any) -> list[Hit]:
        self.calls.append({"mode": mode, "k": k, **kw})
        if mode == "rerank" and self.rerank_error:
            raise self.rerank_error
        return self.hits[:k]


def sections(chunk_ids: list[str], **_: Any) -> dict[str, SectionInfo]:
    return {
        c: SectionInfo(
            c, c.split("#")[0], f"Title {c}", f"Heading {c}", False, f"Text of {c}. Rate 4.69%"
        )
        for c in chunk_ids
    }


def make_runtime(
    settings: Settings,
    client: FakeClient,
    retriever: FakeRetriever,
    *,
    reranker_check: Any = lambda s: None,
) -> Runtime:
    return Runtime(
        settings=settings,
        client=client,
        threshold=THRESHOLD,
        prompt_text=PROMPT,
        prompt_sha256="0" * 64,
        retrieve_fn=retriever,
        sections_fn=sections,
        reranker_check=reranker_check,
    )
