"""Deterministic fake embedder for ingest tests (no network)."""

import hashlib
import math

from rag_agent.errors import PolicyBlockedError
from rag_agent.llm_client import EmbedResult


def fake_vector(text: str, dim: int = 768) -> list[float]:
    raw: list[float] = []
    counter = 0
    while len(raw) < dim:
        digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
        raw.extend(b / 255.0 - 0.5 for b in digest)
        counter += 1
    raw = raw[:dim]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


class FakeEmbedder:
    def __init__(self, fail_at: int | None = None, dim: int = 768):
        self.calls: list[list[str]] = []
        self.fail_at = fail_at  # 0-based index of the call that raises
        self.dim = dim

    def embed(self, texts: list[str]) -> EmbedResult:
        index = len(self.calls)
        self.calls.append(list(texts))
        if self.fail_at is not None and index == self.fail_at:
            raise PolicyBlockedError("blocked by test policy", 403)
        return EmbedResult(
            vectors=[fake_vector(t, self.dim) for t in texts], model="fake", latency_ms=1.0
        )
