"""Hybrid retrieval over the ingested chunks: BM25, vector and RRF. Read-only.

BM25 runs in this process over chunks.index_text loaded from the database. Vector search is an
exact pgvector scan. The query is embedded through LLMClient.embed only (the single model entry
point), once per call, and not at all in bm25 mode or when `query_vector` is given.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from rag_agent import store as default_store
from rag_agent.bm25 import BM25Index
from rag_agent.config import load_settings
from rag_agent.ingest import QUERY_PREFIX, validate_vector
from rag_agent.llm_client import LLMClient
from rag_agent.rerank import rerank_scores

# --- parameters: one block, not tuned ---------------------------------------------------------
BM25_K1 = 1.2
BM25_B = 0.75
RRF_K = 60
CANDIDATES = 50
DEFAULT_K = 10
RERANK_CANDIDATES = 20  # provisional; set after the dev sweep (make eval-rerank-sweep)
RERANK_GRID = (10, 20, 30, 50)
RERANK_MAX_BATCH = 64  # keep equal to --max-client-batch-size in docker-compose.yml
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "my",
        "of",
        "on",
        "or",
        "the",
        "to",
        "what",
        "when",
        "which",
        "who",
        "will",
        "with",
        "you",
        "your",
    }
)
# ----------------------------------------------------------------------------------------------

MODES = ("bm25", "vector", "hybrid", "rerank")


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    doc_id: str
    heading: str
    status: str
    text: str
    rank: int
    score: float
    bm25_rank: int | None
    bm25_score: float | None
    vector_rank: int | None
    vector_score: float | None
    rrf_rank: int | None = None  # rank in the hybrid candidate list (rerank mode only)


@dataclass
class _Entry:
    signature: Any
    index: BM25Index
    chunks: dict[str, default_store.ChunkText]


_CACHE: dict[tuple[str, str], _Entry] = {}


def clear_cache() -> None:
    _CACHE.clear()


def rrf(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Fuse ranked id lists. Sort by rrf desc, best single-list rank asc, chunk_id asc."""
    score: dict[str, float] = {}
    best: dict[str, int] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            score[chunk_id] = score.get(chunk_id, 0.0) + 1.0 / (k + rank)
            best[chunk_id] = min(best.get(chunk_id, rank), rank)
    order = sorted(score, key=lambda c: (-score[c], best[c], c))
    return [(c, score[c]) for c in order]


def _index(collection: str, dsn: str, store: Any) -> _Entry:
    sig = store.collection_signature(collection, dsn=dsn)
    entry = _CACHE.get((collection, dsn))
    if entry is None or entry.signature != sig:
        chunks = store.load_chunks(collection, dsn=dsn)
        index = BM25Index({c.chunk_id: c.index_text for c in chunks}, BM25_K1, BM25_B, STOPWORDS)
        entry = _Entry(sig, index, {c.chunk_id: c for c in chunks})
        _CACHE[(collection, dsn)] = entry
    return entry


def embed_query(query: str, *, client: Any = None) -> list[float]:
    """Embed one query (QUERY_PREFIX + query) through LLMClient.embed; the single vector."""
    if client is None:
        client = LLMClient(load_settings())
    res = client.embed([QUERY_PREFIX + query])
    if len(res.vectors) != 1:
        raise ValueError(f"expected 1 query vector, got {len(res.vectors)}")
    return res.vectors[0]


def retrieve(
    query: str,
    mode: str = "hybrid",
    k: int = DEFAULT_K,
    collection: str = "main",
    include_superseded: bool = True,
    query_vector: list[float] | None = None,
    *,
    client: Any = None,
    store: Any = default_store,
    dsn: str | None = None,
    rerank_n: int | None = None,
    reranker_url: str | None = None,
    rerank_timeout: float | None = None,
    max_batch: int = RERANK_MAX_BATCH,
) -> list[Hit]:
    """Top-`k` hits. `score` is the BM25 score, the cosine, the RRF score or the rerank score.

    `dsn=None` means load_settings().database_url (never the bare DATABASE_URL variable).
    `client` is anything with embed(list[str]) (default LLMClient(settings)).
    mode "rerank": hybrid candidates (k = rerank_n or RERANK_CANDIDATES) reordered by the local
    cross-encoder; a reranker failure raises RerankError, it never returns hybrid hits.
    """
    if not query or not query.strip():
        raise ValueError("query is empty")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if k < 1:
        raise ValueError("k must be >= 1")
    n_cand = rerank_n or RERANK_CANDIDATES
    if mode == "rerank" and n_cand < 1:
        raise ValueError("rerank_n must be >= 1")

    settings = None
    if dsn is None:
        settings = load_settings()
        dsn = settings.database_url

    bm25_list: list[tuple[str, float]] = []
    meta: dict[str, Any] = {}
    index_texts: dict[str, str] = {}
    if mode in ("bm25", "hybrid", "rerank"):
        entry = _index(collection, dsn, store)
        meta.update(entry.chunks)
        index_texts = {c: m.index_text for c, m in entry.chunks.items()}
        keep = None
        if not include_superseded:
            keep = {c for c, m in entry.chunks.items() if m.status == "current"}
        bm25_list = entry.index.top(query, CANDIDATES, keep)

    vec_list: list[Any] = []
    if mode in ("vector", "hybrid", "rerank"):
        if query_vector is None:
            if client is None:
                client = LLMClient(settings or load_settings())
            query_vector = embed_query(query, client=client)
        vec_list = store.vector_search(
            collection, validate_vector(list(query_vector)), CANDIDATES, include_superseded, dsn=dsn
        )
        meta.update({m.chunk_id: m for m in vec_list})

    b_rank = {c: i for i, (c, _) in enumerate(bm25_list, start=1)}
    b_score = dict(bm25_list)
    v_rank = {m.chunk_id: i for i, m in enumerate(vec_list, start=1)}
    v_score = {m.chunk_id: m.score for m in vec_list}

    if mode == "bm25":
        final = list(bm25_list)
    elif mode == "vector":
        final = [(m.chunk_id, m.score) for m in vec_list]
    else:
        final = rrf([[c for c, _ in bm25_list], [m.chunk_id for m in vec_list]])

    rrf_rank: dict[str, int] = {}
    if mode == "rerank":
        cand = [c for c, _ in final[:n_cand]]
        rrf_rank = {c: i for i, c in enumerate(cand, start=1)}
        if reranker_url is None or rerank_timeout is None:
            settings = settings or load_settings()
            reranker_url = reranker_url or settings.reranker_url
            rerank_timeout = rerank_timeout or settings.rerank_timeout_s
        scores = rerank_scores(
            reranker_url,
            query,
            [index_texts[c] for c in cand],
            max_batch,
            rerank_timeout,
        )
        final = sorted(
            zip(cand, scores, strict=True),
            key=lambda cs: (-round(cs[1], 6), rrf_rank[cs[0]], cs[0]),
        )

    hits = []
    for rank, (chunk_id, score) in enumerate(final[:k], start=1):
        m = meta[chunk_id]
        hits.append(
            Hit(
                chunk_id=chunk_id,
                doc_id=m.doc_id,
                heading=m.heading,
                status=m.status,
                text=m.body,
                rank=rank,
                score=score,
                bm25_rank=b_rank.get(chunk_id),
                bm25_score=b_score.get(chunk_id),
                vector_rank=v_rank.get(chunk_id),
                vector_score=v_score.get(chunk_id),
                rrf_rank=rrf_rank.get(chunk_id),
            )
        )
    return hits


def all_index_texts(
    collection: str = "main", *, store: Any = default_store, dsn: str | None = None
) -> list[tuple[str, str]]:
    """(chunk_id, index_text) for every chunk of the collection, ordered by chunk_id."""
    if dsn is None:
        dsn = load_settings().database_url
    return [(c.chunk_id, c.index_text) for c in store.load_chunks(collection, dsn=dsn)]
