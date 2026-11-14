"""Check the local reranker: pinned revision, a fixed sanity pair, and chunk token lengths.

Needs the reranker (docker compose up -d reranker) and the ingested database. Prints counts and
scores only, never chunk text. Exit 1 on any failure.
"""

from __future__ import annotations

import sys

from rag_agent.config import ConfigError, load_settings
from rag_agent.rerank import (
    RerankError,
    check_pin,
    percentile,
    post_json,
    rerank_scores,
    reranker_info,
)
from rag_agent.retrieve import all_index_texts

PAIR_QUERY = "What is the maximum total debt service ratio?"
PAIR_TEXTS = [
    "The total debt service ratio must not exceed the policy limit.",
    "The office is closed on statutory holidays.",
]
TRUNCATION_MARGIN = 64


def token_counts(url: str, texts: list[str], max_batch: int, timeout: float) -> list[int]:
    """Token count per text from POST /tokenize (a list of token objects per input)."""
    counts: list[int] = []
    for i in range(0, len(texts), max_batch):
        batch = texts[i : i + max_batch]
        data = post_json(url, "/tokenize", {"inputs": batch, "add_special_tokens": True}, timeout)
        if not isinstance(data, list) or len(data) != len(batch):
            raise RerankError("reranker /tokenize returned an unexpected shape")
        for toks in data:
            if not isinstance(toks, list):
                raise RerankError("reranker /tokenize returned an unexpected shape")
            counts.append(len(toks))
    return counts


def run(url: str, rev: str, timeout: float, chunks: list[tuple[str, str]] | None = None) -> int:
    try:
        info = reranker_info(url, timeout)
        check_pin(info, rev)
        for key in (
            "model_id",
            "model_sha",
            "version",
            "max_input_length",
            "max_client_batch_size",
        ):
            print(f"{key} {info.get(key)}")
        max_batch = int(info.get("max_client_batch_size") or 64)
        max_len = int(info.get("max_input_length") or 512)

        scores = rerank_scores(url, PAIR_QUERY, PAIR_TEXTS, max_batch, timeout)
        print(f"pair score relevant {scores[0]:.4f}")
        print(f"pair score unrelated {scores[1]:.4f}")
        if not scores[0] > scores[1]:
            print("[FAIL] the relevant text did not score higher")
            return 1

        if chunks is None:
            chunks = all_index_texts("main")
        counts = token_counts(url, [t for _, t in chunks], max_batch, timeout)
        limit = max_len - TRUNCATION_MARGIN
        over = sum(c > limit for c in counts)
        print(f"n chunks {len(counts)}")
        print(f"tokens max {max(counts)} p95 {percentile([float(c) for c in counts], 95):.0f}")
        print(f"chunks over {limit} tokens (may be truncated with a typical query): {over}")
    except (RerankError, ConfigError) as exc:
        print(f"[FAIL] {exc}")
        return 1
    except Exception as exc:  # database or other local failure
        print(f"[FAIL] {type(exc).__name__}")
        return 1
    print("reranker ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        s = load_settings()
    except ConfigError as exc:
        print(f"[FAIL] config: {exc}")
        return 1
    return run(s.reranker_url, s.reranker_model_rev, s.rerank_timeout_s)


if __name__ == "__main__":
    sys.exit(main())
