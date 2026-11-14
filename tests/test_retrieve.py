import math
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from fake_embed import FakeEmbedder, fake_vector
from rag_agent import retrieve as retrieve_mod
from rag_agent.ingest import DOC_PREFIX
from rag_agent.retrieve import CANDIDATES, DEFAULT_K, RRF_K, STOPWORDS, retrieve, rrf
from rag_agent.store import ChunkText, VectorMatch

DSN = "postgresql://fake.invalid/none"


def chunk(chunk_id, status, text):
    doc_id = chunk_id.split("#")[0]
    return ChunkText(chunk_id, doc_id, chunk_id.split("#")[1], status, text, f"{doc_id}\n{text}")


CHUNKS = [
    chunk("POL-003#ratio-limits", "current", "The maximum GDS ratio is set by policy."),
    chunk("POL-003#exceptions", "current", "Exceptions to the ratio limits need approval."),
    chunk("POL-003-V1#former-limits", "superseded", "The former maximum GDS ratio limits."),
    chunk("POL-003-V1#summary", "superseded", "Superseded ratio guidance for older files."),
    chunk("RATE-002#fees", "current", "The discharge fee and the transfer fee are listed."),
    chunk("FAQ-002#extra", "current", "Yes, you can pay extra on your mortgage each year."),
    chunk("PRC-005#form-exc-100", "current", "Form EXC-100 records an exception request."),
]


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


class FakeStore:
    def __init__(self, chunks=CHUNKS):
        self.chunks = list(chunks)
        self.vectors = {c.chunk_id: fake_vector(DOC_PREFIX + c.index_text) for c in chunks}
        self.sig = (len(chunks), 1)
        self.calls: list[tuple] = []

    def collection_signature(self, collection, dsn=None):
        self.calls.append(("signature", collection, dsn))
        return self.sig

    def load_chunks(self, collection, dsn=None):
        self.calls.append(("load", collection, dsn))
        return list(self.chunks)

    def vector_search(self, collection, vector, limit, include_superseded=True, dsn=None):
        self.calls.append(("vector", collection, dsn, include_superseded))
        rows = [
            (1 - cosine(vector, self.vectors[c.chunk_id]), c)
            for c in self.chunks
            if include_superseded or c.status == "current"
        ]
        rows.sort(key=lambda r: (r[0], r[1].chunk_id))
        return [
            VectorMatch(c.chunk_id, c.doc_id, c.heading, c.status, c.body, 1 - d)
            for d, c in rows[:limit]
        ]


@pytest.fixture(autouse=True)
def fresh_cache():
    retrieve_mod.clear_cache()
    yield
    retrieve_mod.clear_cache()


def run(query, store=None, client=None, **kw):
    return retrieve(
        query, store=store or FakeStore(), client=client or FakeEmbedder(), dsn=DSN, **kw
    )


def test_parameter_block():
    assert (RRF_K, CANDIDATES, DEFAULT_K) == (60, 50, 10)
    assert {"the", "what", "your", "a"} <= STOPWORDS and len(STOPWORDS) == 34


def test_rrf_with_tie():
    a = ["a", "b", "c"]
    b = ["b", "a", "d"]
    fused = rrf([a, b])
    # a and b: 1/61 + 1/62 each (tie, both best rank 1) -> chunk_id order: a, b.
    # c and d: 1/63 each (tie, best rank 3) -> c, d.
    assert [c for c, _ in fused] == ["a", "b", "c", "d"]
    assert fused[0][1] == fused[1][1] == pytest.approx(1 / 61 + 1 / 62)
    assert fused[2][1] == fused[3][1] == pytest.approx(1 / 63)


def test_rrf_prefers_chunks_in_both_lists():
    fused = rrf([["x", "y"], ["z", "y"]])
    # y: 1/62 + 1/62 = 0.03226 beats x and z: 1/61 = 0.01639 (tie -> x before z)
    assert [c for c, _ in fused] == ["y", "x", "z"]
    assert fused[0][1] == pytest.approx(2 / 62)
    assert math.isclose(fused[1][1], 1 / 61)


def test_embedder_input_and_call_counts():
    emb = FakeEmbedder()
    run("maximum GDS ratio", client=emb, mode="bm25")
    assert emb.calls == []
    run("maximum GDS ratio", client=emb, mode="hybrid")
    assert len(emb.calls) == 1 and len(emb.calls[0]) == 1
    assert emb.calls[0][0] == "search_query: maximum GDS ratio"
    run("maximum GDS ratio", client=emb, mode="vector")
    assert len(emb.calls) == 2
    run("maximum GDS ratio", client=emb, mode="hybrid", query_vector=fake_vector("q"))
    assert len(emb.calls) == 2


def test_bm25_mode_builds_no_client(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("LLMClient must not be built")

    monkeypatch.setattr(retrieve_mod, "LLMClient", boom)
    hits = retrieve("maximum GDS ratio", "bm25", store=FakeStore(), dsn=DSN)
    assert hits and all(h.vector_rank is None for h in hits)


def test_hit_fields_by_mode():
    store = FakeStore()
    h = run("maximum GDS ratio", store=store, mode="hybrid")[0]
    assert h.rank == 1 and h.text and h.bm25_rank is not None and h.vector_rank is not None
    b = run("maximum GDS ratio", store=store, mode="bm25")
    assert [x.rank for x in b] == list(range(1, len(b) + 1))
    assert all(x.bm25_score and x.vector_score is None and x.score == x.bm25_score for x in b)
    v = run("maximum GDS ratio", store=store, mode="vector", k=3)
    assert len(v) == 3 and all(x.score == x.vector_score and x.bm25_rank is None for x in v)


def test_include_superseded_false_drops_v1_chunks():
    for mode in ("bm25", "vector", "hybrid"):
        every = run("maximum GDS ratio former limits", mode=mode, k=50)
        assert any("-V1#" in h.chunk_id for h in every), mode
        current = run("maximum GDS ratio former limits", mode=mode, k=50, include_superseded=False)
        assert current and not any("-V1#" in h.chunk_id for h in current), mode
        assert all(h.status == "current" for h in current)


def test_filter_applies_before_the_candidate_cut():
    many = [chunk(f"OLD-{i:03d}#s", "superseded", "ratio ratio ratio") for i in range(60)]
    new = [chunk("NEW-001#s", "current", "ratio and many other words here to dilute it")]
    hits = run("ratio", store=FakeStore(many + new), mode="bm25", include_superseded=False)
    assert [h.chunk_id for h in hits] == ["NEW-001#s"]


def test_default_collection_is_main():
    store = FakeStore()
    run("maximum GDS ratio", store=store)
    assert {c[1] for c in store.calls} == {"main"}


def test_same_query_twice_is_identical_and_cached():
    store = FakeStore()
    first = run("discharge fee", store=store)
    second = run("discharge fee", store=store)
    assert first == second
    assert sum(1 for c in store.calls if c[0] == "load") == 1
    store.sig = (len(CHUNKS), 2)  # collection re-ingested
    run("discharge fee", store=store)
    assert sum(1 for c in store.calls if c[0] == "load") == 2


@pytest.mark.parametrize("q", ["", "   ", "\n\t"])
def test_blank_query_raises(q):
    with pytest.raises(ValueError):
        run(q)


def test_bad_mode_and_k_raise():
    with pytest.raises(ValueError):
        run("fee", mode="nope")
    with pytest.raises(ValueError):
        run("fee", k=0)


def test_all_stopword_query_hybrid_falls_back_to_vector():
    store = FakeStore()
    assert run("what is the", store=store, mode="bm25") == []
    hybrid = run("what is the", store=store, mode="hybrid", k=5)
    vector = run("what is the", store=store, mode="vector", k=5)
    assert [h.chunk_id for h in hybrid] == [h.chunk_id for h in vector]
    assert all(h.bm25_rank is None for h in hybrid)


def test_dsn_defaults_to_settings_not_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://wrong.invalid/env")
    monkeypatch.setattr(
        retrieve_mod, "load_settings", lambda: SimpleNamespace(database_url="postgresql://s/db")
    )
    store = FakeStore()
    retrieve("maximum GDS ratio", store=store, client=FakeEmbedder())
    assert {c[2] for c in store.calls} == {"postgresql://s/db"}


def test_explicit_dsn_wins_and_skips_settings(monkeypatch):
    def boom():
        raise AssertionError("load_settings must not be called")

    monkeypatch.setattr(retrieve_mod, "load_settings", boom)
    store = FakeStore()
    retrieve("maximum GDS ratio", store=store, client=FakeEmbedder(), dsn="postgresql://x/y")
    assert {c[2] for c in store.calls} == {"postgresql://x/y"}


def test_bad_query_vector_is_rejected():
    with pytest.raises(ValueError):
        run("fee", mode="vector", query_vector=[0.1, 0.2])


def test_sources_are_read_only():
    root = Path(retrieve_mod.__file__).parent
    for name in ("bm25.py", "retrieve.py"):
        src = (root / name).read_text(encoding="utf-8")
        assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", src), name


class FakeReranker:
    def __init__(self, fn=None):
        self.calls: list[tuple] = []
        self.fn = fn or (lambda texts: [0.5] * len(texts))

    def __call__(self, url, query, texts, max_batch, timeout):
        self.calls.append((url, query, list(texts), max_batch, timeout))
        return self.fn(texts)


def rerank_run(query, rr, monkeypatch, **kw):
    monkeypatch.setattr(retrieve_mod, "rerank_scores", rr)
    return run(query, mode="rerank", reranker_url="http://r.invalid", rerank_timeout=3.0, **kw)


def test_rerank_params_and_candidate_texts(monkeypatch):
    assert retrieve_mod.RERANK_CANDIDATES == 10 and retrieve_mod.RERANK_GRID == (10, 20, 30, 50)
    rr = FakeReranker()
    hits = rerank_run("maximum GDS ratio", rr, monkeypatch, rerank_n=4)
    url, query, texts, max_batch, timeout = rr.calls[0]
    assert (url, query, max_batch, timeout) == ("http://r.invalid", "maximum GDS ratio", 64, 3.0)
    hybrid = run("maximum GDS ratio", mode="hybrid", k=4)
    assert len(texts) == 4
    by_id = {c.chunk_id: c.index_text for c in CHUNKS}
    assert texts == [by_id[h.chunk_id] for h in hybrid]
    assert [h.rrf_rank for h in hits] == [1, 2, 3, 4]


def test_rerank_orders_by_score_then_rrf_rank(monkeypatch):
    # candidate 3 gets the best score; 1e-7 differences vanish at 6 decimals
    scores = [0.5, 0.5 + 1e-7, 0.9, 0.5 + 2e-7]
    rr = FakeReranker(lambda texts: scores[: len(texts)])
    hits = rerank_run("maximum GDS ratio", rr, monkeypatch, rerank_n=4)
    hybrid = [h.chunk_id for h in run("maximum GDS ratio", mode="hybrid", k=4)]
    assert [h.chunk_id for h in hits] == [hybrid[2], hybrid[0], hybrid[1], hybrid[3]]
    assert hits[0].score == 0.9 and [h.rank for h in hits] == [1, 2, 3, 4]
    assert [h.rrf_rank for h in hits] == [3, 1, 2, 4]


def test_rerank_k_limits_output_not_candidates(monkeypatch):
    rr = FakeReranker()
    hits = rerank_run("maximum GDS ratio", rr, monkeypatch, rerank_n=5, k=2)
    assert len(hits) == 2 and len(rr.calls[0][2]) == 5


def test_rerank_reuses_given_query_vector(monkeypatch):
    emb = FakeEmbedder()
    rerank_run("maximum GDS ratio", FakeReranker(), monkeypatch, client=emb)
    assert len(emb.calls) == 1
    rerank_run(
        "maximum GDS ratio", FakeReranker(), monkeypatch, client=emb, query_vector=fake_vector("q")
    )
    assert len(emb.calls) == 1


def test_embed_failure_never_reaches_the_reranker(monkeypatch):
    rr = FakeReranker()
    with pytest.raises(Exception, match="blocked"):
        rerank_run("maximum GDS ratio", rr, monkeypatch, client=FakeEmbedder(fail_at=0))
    assert rr.calls == []


def test_rerank_failure_raises_and_returns_no_hybrid_hits(monkeypatch):
    from rag_agent.rerank import RerankError

    def boom(texts):
        raise RerankError("down")

    with pytest.raises(RerankError):
        rerank_run("maximum GDS ratio", FakeReranker(boom), monkeypatch)


def test_all_index_texts_comes_from_the_store():
    store = FakeStore()
    pairs = retrieve_mod.all_index_texts("main", store=store, dsn=DSN)
    assert pairs == [(c.chunk_id, c.index_text) for c in CHUNKS]
    assert store.calls[-1] == ("load", "main", DSN)
