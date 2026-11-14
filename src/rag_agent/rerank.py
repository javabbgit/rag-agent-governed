"""Client for the local cross-encoder reranker (text-embeddings-inference, HTTP, stdlib only).

Not a model entry point for the gateway: the reranker is a local service and sees only chunk
index texts and the query. The query and texts are never logged or stored here.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from typing import Any


class RerankError(Exception):
    """Any reranker failure: unreachable, timeout, bad status, malformed or invalid response."""


def _open(req: urllib.request.Request, timeout: float) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RerankError(f"reranker request failed: {type(exc).__name__}") from None


def post_json(url: str, path: str, body: dict[str, Any], timeout: float) -> Any:
    req = urllib.request.Request(
        url.rstrip("/") + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    status, raw = _open(req, timeout)
    if status != 200:
        raise RerankError(f"reranker {path} returned HTTP {status}")
    try:
        return json.loads(raw)
    except ValueError:
        raise RerankError(f"reranker {path} returned malformed JSON") from None


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile (p in 0..100) of a non-empty list."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    idx = max(0, math.ceil(p / 100 * len(ordered)) - 1)
    return ordered[min(idx, len(ordered) - 1)]


def reranker_info(url: str, timeout: float) -> dict[str, Any]:
    """GET /info: model_id, model_sha, max_input_length, max_client_batch_size, version."""
    req = urllib.request.Request(url.rstrip("/") + "/info", method="GET")
    status, raw = _open(req, timeout)
    if status != 200:
        raise RerankError(f"reranker /info returned HTTP {status}")
    try:
        data = json.loads(raw)
    except ValueError:
        raise RerankError("reranker /info returned malformed JSON") from None
    if not isinstance(data, dict):
        raise RerankError("reranker /info returned an unexpected shape")
    return {
        k: data.get(k)
        for k in ("model_id", "model_sha", "max_input_length", "max_client_batch_size", "version")
    }


def check_pin(info: dict[str, Any], rev: str) -> None:
    """Pass when the served model is the pinned revision; else RerankError naming both."""
    sha = info.get("model_sha")
    model_id = info.get("model_id")
    if sha == rev:
        return
    if isinstance(model_id, str) and model_id.endswith("/snapshots/" + rev):
        return
    raise RerankError(f"reranker model mismatch: expected {rev}, got sha={sha} id={model_id}")


def rerank_scores(
    url: str, query: str, texts: list[str], max_batch: int, timeout: float
) -> list[float]:
    """Scores in [0, 1] in input order, from one POST /rerank."""
    if len(texts) > max_batch:
        raise RerankError(f"{len(texts)} texts exceed the reranker batch limit {max_batch}")
    data = post_json(
        url,
        "/rerank",
        {
            "query": query,
            "texts": texts,
            "truncate": True,
            "raw_scores": False,
            "return_text": False,
        },
        timeout,
    )
    if not isinstance(data, list):
        raise RerankError("reranker /rerank returned an unexpected shape")
    scores: dict[int, float] = {}
    for item in data:
        if not isinstance(item, dict):
            raise RerankError("reranker /rerank returned a malformed result")
        idx, score = item.get("index"), item.get("score")
        if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < len(texts):
            raise RerankError("reranker /rerank returned a bad index")
        if idx in scores:
            raise RerankError("reranker /rerank returned a duplicate index")
        if isinstance(score, bool) or not isinstance(score, int | float):
            raise RerankError("reranker /rerank returned a non-numeric score")
        if not 0.0 <= float(score) <= 1.0:
            raise RerankError("reranker /rerank returned a score outside [0, 1]")
        scores[idx] = float(score)
    if len(scores) != len(texts):
        raise RerankError("reranker /rerank is missing an index")
    return [scores[i] for i in range(len(texts))]
