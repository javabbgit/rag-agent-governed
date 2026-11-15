"""DB tests: need Postgres with pgvector and DATABASE_URL. Run with `make test-db`."""

import os
from pathlib import Path

import psycopg
import pytest

from fake_embed import FakeEmbedder, fake_vector
from rag_agent import retrieve as retrieve_mod
from rag_agent import store
from rag_agent.config import Settings
from rag_agent.ingest import DOC_PREFIX, run_ingest
from rag_agent.retrieve import retrieve

pytestmark = pytest.mark.db

COLLECTION = "pytest"
DSN = os.environ.get("DATABASE_URL")
FIXTURE_DOCS = Path(__file__).parent / "fixtures" / "corpus" / "docs"


def _clean() -> None:
    with psycopg.connect(DSN) as conn:
        conn.execute("DELETE FROM documents WHERE collection = %s", (COLLECTION,))
        conn.execute("DELETE FROM ingest_runs WHERE collection = %s", (COLLECTION,))


@pytest.fixture(autouse=True)
def clean_db():
    if not DSN:
        pytest.skip("DATABASE_URL is not set")
    store.init_schema(DSN)
    _clean()
    retrieve_mod.clear_cache()
    yield
    _clean()
    retrieve_mod.clear_cache()


@pytest.fixture
def ingested(settings: Settings, tmp_path):
    s = settings.model_copy(update={"embed_dim": 768, "database_url": DSN})
    code = run_ingest(
        s,
        COLLECTION,
        FIXTURE_DOCS,
        client=FakeEmbedder(),
        sleep=lambda _: None,
        reports_dir=tmp_path / "reports",
    )
    assert code == 0


def snapshot():
    with psycopg.connect(DSN) as conn:
        return {
            t: conn.execute(q, (COLLECTION,)).fetchall()
            for t, q in {
                "chunks": "SELECT chunk_id, content_sha256, ingested_at FROM chunks"
                " WHERE collection = %s ORDER BY 1",
                "documents": "SELECT doc_id, source_sha256 FROM documents"
                " WHERE collection = %s ORDER BY 1",
                "runs": "SELECT run_id FROM ingest_runs WHERE collection = %s ORDER BY 1",
            }.items()
        }


def test_vector_search_orders_by_distance_then_chunk_id(ingested):
    chunks = store.load_chunks(COLLECTION, DSN)
    assert len(chunks) > 3
    target = chunks[2]
    qv = fake_vector(DOC_PREFIX + target.index_text)  # the chunk's own embedding input
    rows = store.vector_search(COLLECTION, qv, 50, True, DSN)
    assert len(rows) == len(chunks)
    assert rows[0].chunk_id == target.chunk_id and rows[0].score == pytest.approx(1.0, abs=1e-5)
    for a, b in zip(rows, rows[1:], strict=False):
        assert a.score >= b.score
        if a.score == b.score:
            assert a.chunk_id < b.chunk_id
    limited = store.vector_search(COLLECTION, qv, 3, True, DSN)
    assert [r.chunk_id for r in limited] == [r.chunk_id for r in rows[:3]]


def test_vector_search_status_filter(ingested):
    qv = fake_vector("anything")
    every = store.vector_search(COLLECTION, qv, 50, True, DSN)
    current = store.vector_search(COLLECTION, qv, 50, False, DSN)
    assert any(r.status == "superseded" for r in every)
    assert current and all(r.status == "current" for r in current)
    assert {r.chunk_id for r in current} == {r.chunk_id for r in every if r.status == "current"}


def test_retrieval_writes_nothing(ingested):
    before = snapshot()
    for mode in ("bm25", "vector", "hybrid"):
        hits = retrieve(
            "debt service ratio limits",
            mode,
            5,
            COLLECTION,
            client=FakeEmbedder(),
            dsn=DSN,
        )
        assert hits
    store.collection_signature(COLLECTION, DSN)
    assert snapshot() == before


def test_bm25_over_database_includes_superseded(ingested):
    hits = retrieve("former limits", "bm25", 10, COLLECTION, dsn=DSN)
    assert any(h.status == "superseded" for h in hits)
    current = retrieve("former limits", "bm25", 10, COLLECTION, False, dsn=DSN)
    assert all(h.status == "current" for h in current)


def test_section_info_returns_text_and_metadata(ingested):
    chunks = store.load_chunks(COLLECTION, DSN)
    ids = [c.chunk_id for c in chunks[:3]]
    info = retrieve_mod.section_info([*ids, "NOPE#missing"], COLLECTION, dsn=DSN)
    assert set(info) == set(ids)
    for i in ids:
        s = info[i]
        assert s.doc_id and s.title and s.section and s.text
    superseded = {
        h.chunk_id
        for h in retrieve("former limits", "bm25", 10, COLLECTION, dsn=DSN)
        if h.status == "superseded"
    }
    if superseded:
        assert all(
            v.superseded
            for v in retrieve_mod.section_info(sorted(superseded), COLLECTION, dsn=DSN).values()
        )
    assert retrieve_mod.section_info([], COLLECTION, dsn=DSN) == {}
