"""DB tests: need Postgres with pgvector and DATABASE_URL. Run with `make test-db`."""

import os

import psycopg
import pytest

from fake_embed import FakeEmbedder
from rag_agent import store
from rag_agent.config import Settings
from rag_agent.ingest import run_ingest
from test_ingest import make_corpus

pytestmark = pytest.mark.db

COLLECTION = "pytest"
DSN = os.environ.get("DATABASE_URL")


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
    yield
    _clean()


@pytest.fixture
def s(settings: Settings) -> Settings:
    return settings.model_copy(update={"embed_dim": 768, "database_url": DSN})


def ingest(s, docs, tmp_path, client=None, **kw):
    client = client or FakeEmbedder()
    code = run_ingest(
        s,
        COLLECTION,
        docs,
        client=client,
        sleep=lambda _: None,
        reports_dir=tmp_path / "reports",
        **kw,
    )
    return code, client


def count(table: str) -> int:
    with psycopg.connect(DSN) as conn:
        return conn.execute(
            f"SELECT count(*) FROM {table} WHERE collection = %s",  # noqa: S608 - fixed names
            (COLLECTION,),
        ).fetchone()[0]


def test_init_schema_twice():
    store.init_schema(DSN)
    store.init_schema(DSN)


def test_ingest_twice_second_embeds_zero(s, tmp_path):
    docs = make_corpus(tmp_path, 3)
    code, client = ingest(s, docs, tmp_path)
    assert code == 0 and len(client.calls) == 4
    assert count("chunks") == 4 and count("documents") == 1
    code, client = ingest(s, docs, tmp_path)
    assert code == 0 and client.calls == []
    with psycopg.connect(DSN) as conn:
        runs = conn.execute(
            "SELECT n_embedded, n_skipped, ok FROM ingest_runs WHERE collection = %s"
            " ORDER BY run_id",
            (COLLECTION,),
        ).fetchall()
    assert runs == [(4, 0, True), (0, 4, True)]


def test_vector_dims_is_768(s, tmp_path):
    ingest(s, make_corpus(tmp_path, 1), tmp_path)
    with psycopg.connect(DSN) as conn:
        dims = conn.execute(
            "SELECT DISTINCT vector_dims(embedding) FROM chunks WHERE collection = %s",
            (COLLECTION,),
        ).fetchall()
    assert dims == [(768,)]


def test_change_one_chunk_embeds_one(s, tmp_path):
    docs = make_corpus(tmp_path, 3)
    ingest(s, docs, tmp_path)
    f = docs / "T-001.md"
    f.write_text(f.read_text().replace("Body number 1.", "Body number one, edited."))
    code, client = ingest(s, docs, tmp_path)
    assert code == 0 and len(client.calls) == 1


def test_removed_doc_deletes_its_chunks(s, tmp_path):
    docs = make_corpus(tmp_path, 2)
    (docs / "T-002.md").write_text(
        (docs / "T-001.md").read_text().replace("T-001", "T-002"), encoding="utf-8"
    )
    ingest(s, docs, tmp_path)
    assert count("documents") == 2 and count("chunks") == 6
    (docs / "T-002.md").unlink()
    code, client = ingest(s, docs, tmp_path)
    assert code == 0 and client.calls == []
    assert count("documents") == 1 and count("chunks") == 3


def test_mid_run_embedding_failure_leaves_tables_unchanged(s, tmp_path):
    docs = make_corpus(tmp_path, 3)
    ingest(s, docs, tmp_path)
    f = docs / "T-001.md"
    f.write_text(
        f.read_text().replace("Body number", "Changed body").replace("Summary text.", "New.")
    )
    with psycopg.connect(DSN) as conn:
        before = conn.execute(
            "SELECT chunk_id, content_sha256 FROM chunks WHERE collection = %s ORDER BY 1",
            (COLLECTION,),
        ).fetchall()
    code, _ = ingest(s, docs, tmp_path, client=FakeEmbedder(fail_at=2))
    assert code == 1
    with psycopg.connect(DSN) as conn:
        after = conn.execute(
            "SELECT chunk_id, content_sha256 FROM chunks WHERE collection = %s ORDER BY 1",
            (COLLECTION,),
        ).fetchall()
        failed = conn.execute(
            "SELECT ok FROM ingest_runs WHERE collection = %s ORDER BY run_id DESC LIMIT 1",
            (COLLECTION,),
        ).fetchone()
    assert before == after and failed == (False,)
    assert count("documents") == 1


def test_fresh_failure_writes_nothing(s, tmp_path):
    code, _ = ingest(s, make_corpus(tmp_path, 3), tmp_path, client=FakeEmbedder(fail_at=1))
    assert code == 1 and count("chunks") == 0 and count("documents") == 0


def test_reorder_refreshes_position_without_embedding(s, tmp_path):
    docs = make_corpus(tmp_path, 3)
    ingest(s, docs, tmp_path)
    f = docs / "T-001.md"
    head, rest = f.read_text().split("## Section 0", 1)
    first, last = rest.split("## Section 2", 1)
    f.write_text(
        head + "## Section 2" + last.rstrip() + "\n\n## Section 0" + first, encoding="utf-8"
    )
    code, client = ingest(s, docs, tmp_path)
    assert code == 0 and client.calls == []
    with psycopg.connect(DSN) as conn:
        pos = dict(
            conn.execute(
                "SELECT chunk_id, position FROM chunks WHERE collection = %s", (COLLECTION,)
            ).fetchall()
        )
    assert (
        pos["T-001#section-2"] == 1 and pos["T-001#section-0"] == 2 and pos["T-001#section-1"] == 3
    )
