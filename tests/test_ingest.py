import json
from pathlib import Path

import pytest

from fake_embed import FakeEmbedder, fake_vector
from rag_agent import ingest
from rag_agent.config import Settings
from rag_agent.ingest import (
    DOC_PREFIX,
    QUERY_PREFIX,
    InvalidVectorError,
    build_corpus,
    content_sha256,
    embed_input,
    run_ingest,
    validate_vector,
)

FIXTURE_DOCS = Path(__file__).parent / "fixtures" / "corpus" / "docs"

POL001_SUMMARY = (
    "This document explains how credit files are reviewed at Demo Mortgage Co. It sets out the "
    "scope of the review, who owns each part of it, and how records are kept."
)


class FakeStore:
    def __init__(self, hashes: dict[str, str] | None = None):
        self.hashes = hashes or {}
        self.write_calls: list[tuple] = []
        self.record_calls: list[dict] = []

    def get_chunk_hashes(self, collection, dsn=None):
        return dict(self.hashes)

    def write_run(
        self, collection, documents, chunks, keep_chunk_ids, dsn=None, skipped_chunks=None
    ):
        self.write_calls.append((collection, documents, chunks, set(keep_chunk_ids)))
        self.skipped = skipped_chunks
        return []

    def record_run(self, collection, **kw):
        self.record_calls.append(kw)


@pytest.fixture
def s768(settings: Settings) -> Settings:
    return settings.model_copy(update={"embed_dim": 768})


def make_corpus(root: Path, n_sections: int) -> Path:
    docs = root / "docs"
    docs.mkdir()
    (docs / "T-001.md").write_text(
        '+++\nid = "T-001"\ntitle = "Test Doc"\ndoc_type = "policy"\nversion = "1.0"\n'
        'status = "current"\neffective_date = 2026-01-01\n+++\n\n# Test Doc\n\nSummary text.\n\n'
        + "".join(f"## Section {i}\n\nBody number {i}.\n\n" for i in range(n_sections)),
        encoding="utf-8",
    )
    return docs


def run(s, docs, store, client, tmp_path, **kw):
    return run_ingest(
        s,
        "pytest",
        docs,
        client=client,
        store=store,
        sleep=lambda _: None,
        reports_dir=tmp_path / "reports",
        **kw,
    )


def test_constants():
    assert DOC_PREFIX == "search_document: "
    assert QUERY_PREFIX == "search_query: "


def test_index_text_and_embed_input_current_and_superseded():
    documents, chunks = build_corpus(FIXTURE_DOCS, "rag-embed", 768)
    by_id = {c.row.chunk_id: c.row for c in chunks}

    cur = by_id["POL-001#summary"]
    assert cur.position == 0 and cur.heading == "summary"
    assert cur.index_text == "POL-001 Credit Policy Overview\n" + POL001_SUMMARY
    assert embed_input(cur.index_text) == (
        "search_document: POL-001 Credit Policy Overview\n" + POL001_SUMMARY
    )
    first_h2 = by_id["POL-001#scope-of-review"]
    assert first_h2.position == 1 and first_h2.heading == "Scope of Review"
    assert first_h2.index_text.startswith("POL-001 Credit Policy Overview\n## Scope of Review")

    old = by_id["POL-003-V1#summary"]
    assert old.index_text.startswith(
        "POL-003-V1 Debt Service Ratio Limits (GDS/TDS) (superseded)\nSuperseded on"
    )
    assert embed_input(old.index_text).startswith(
        "search_document: POL-003-V1 Debt Service Ratio Limits (GDS/TDS) (superseded)\n"
    )
    docs = {d.doc_id: d for d in documents}
    assert docs["POL-003-V1"].superseded_by == "POL-003"
    assert docs["POL-003-V1"].supersedes is None
    assert docs["POL-001"].superseded_by is None


def test_hash_changes_with_text_and_model():
    base = content_sha256("search_document: x", "rag-embed", 768)
    assert base == content_sha256("search_document: x", "rag-embed", 768)
    assert base != content_sha256("search_document: y", "rag-embed", 768)
    assert base != content_sha256("search_document: x", "other-model", 768)
    assert len(base) == 64


def test_767_dim_vector_fails():
    assert validate_vector(fake_vector("a"))
    with pytest.raises(InvalidVectorError):
        validate_vector(fake_vector("a", 767))
    with pytest.raises(InvalidVectorError):
        validate_vector([float("nan")] * 768)


def test_767_dim_from_embedder_fails_run(s768, tmp_path):
    docs = make_corpus(tmp_path, 2)
    store, client = FakeStore(), FakeEmbedder(dim=767)
    assert run(s768, docs, store, client, tmp_path) == 1
    assert store.write_calls == []
    assert store.record_calls[0]["ok"] is False


def test_embed_dim_1024_fails_before_any_embed(settings, tmp_path):
    docs = make_corpus(tmp_path, 2)
    s = settings.model_copy(update={"embed_dim": 1024})
    store, client = FakeStore(), FakeEmbedder()
    assert run(s, docs, store, client, tmp_path) == 2
    assert client.calls == [] and store.write_calls == [] and store.record_calls == []


def test_gateway_without_key_exits_2(s768, tmp_path):
    docs = make_corpus(tmp_path, 2)
    s = s768.model_copy(update={"openai_api_key": "", "embed_api_key": None})
    store, client = FakeStore(), FakeEmbedder()
    assert run(s, docs, store, client, tmp_path) == 2
    assert client.calls == []


def test_error_at_chunk_10_writes_nothing_and_report_names_it(s768, tmp_path):
    docs = make_corpus(tmp_path, 14)  # summary + 14 sections = 15 chunks
    store, client = FakeStore(), FakeEmbedder(fail_at=9)
    assert run(s768, docs, store, client, tmp_path) == 1
    assert store.write_calls == []
    assert len(client.calls) == 10  # stops at the failure, no retry
    assert store.record_calls[0]["ok"] is False
    assert store.record_calls[0]["n_embedded"] == 0
    report = json.loads(next((tmp_path / "reports").glob("ingest_*.json")).read_text())
    failed = [c for c in report["chunks"] if not c["ok"]]
    assert [c["chunk_id"] for c in failed] == ["T-001#section-8"]
    assert failed[0]["error_class"] == "PolicyBlockedError"
    assert report["ok"] is False and report["n_errors"] == 1


def test_success_then_skip_and_report_has_no_text_or_vectors(s768, tmp_path, capsys):
    docs = make_corpus(tmp_path, 3)
    store, client = FakeStore(), FakeEmbedder()
    assert run(s768, docs, store, client, tmp_path) == 0
    assert len(client.calls) == 4 and len(store.write_calls) == 1
    _, documents, chunks, keep = store.write_calls[0]
    assert len(documents) == 1 and len(chunks) == 4 and len(keep) == 4
    assert all(len(c.embedding) == 768 for c in chunks)
    assert "ingest pytest: docs 1, chunks 4, embedded 4, skipped 0, deleted 0, errors 0, ok" in (
        capsys.readouterr().out
    )

    raw = next((tmp_path / "reports").glob("ingest_*.json")).read_text()
    report = json.loads(raw)
    for key in ("body", "index_text", "text", "embedding", "vector", "api_key"):
        assert key not in raw
    assert "Body number" not in raw and "sk-test" not in raw
    assert set(report) >= {
        "started_utc", "finished_utc", "mode", "embed_base_url_host", "embed_model", "embed_dim",
        "collection", "docs_dir", "git_commit", "git_dirty", "n_docs", "n_chunks", "n_embedded",
        "n_skipped", "n_deleted", "deleted_ids", "n_errors", "ok", "chunks",
    }  # fmt: skip
    assert report["embed_base_url_host"] == "gateway.invalid"
    assert set(report["chunks"][0]) == {"chunk_id", "sent_utc", "latency_ms", "ok", "error_class"}

    # second run: stored hashes match, nothing is embedded
    stored = {c.chunk_id: c.content_sha256 for c in chunks}
    store2, client2 = FakeStore(stored), FakeEmbedder()
    assert run(s768, docs, store2, client2, tmp_path) == 0
    assert client2.calls == []
    assert store2.record_calls[0]["n_skipped"] == 4

    # --rebuild ignores stored hashes
    store3, client3 = FakeStore(stored), FakeEmbedder()
    assert run(s768, docs, store3, client3, tmp_path, rebuild=True) == 0
    assert len(client3.calls) == 4


def test_chunk_id_set_must_match_frozen_csv(s768, tmp_path, monkeypatch):
    docs = make_corpus(tmp_path, 2)
    csv_path = tmp_path / "chunks.csv"
    csv_path.write_text("chunk_id,doc_id\nT-001#summary,T-001\n", encoding="utf-8")
    monkeypatch.setattr(ingest, "REPO_ROOT", tmp_path)
    (tmp_path / "corpus").mkdir()
    (tmp_path / "corpus" / "docs").symlink_to(docs)
    store, client = FakeStore(), FakeEmbedder()
    code = run(s768, docs, store, client, tmp_path, chunks_csv=csv_path)
    assert code == 1 and client.calls == []


def test_skipped_chunks_get_non_vector_columns_refreshed(s768, tmp_path):
    docs = make_corpus(tmp_path, 3)
    _, prepared = build_corpus(docs, s768.embed_model, 768)
    stored = {p.row.chunk_id: p.row.content_sha256 for p in prepared}
    # Move Section 2 before Section 0 and edit Section 1; the other chunks keep their hash.
    f = docs / "T-001.md"
    head, rest = f.read_text().split("## Section 0", 1)
    sec0, rest = rest.split("## Section 1", 1)
    sec1, sec2 = rest.split("## Section 2", 1)
    f.write_text(
        head + "## Section 2" + sec2.rstrip() + "\n\n## Section 0" + sec0
        + "## Section 1" + sec1.replace("Body number 1.", "Edited."),
        encoding="utf-8",
    )  # fmt: skip
    store, client = FakeStore(stored), FakeEmbedder()
    assert run(s768, docs, store, client, tmp_path) == 0
    assert len(client.calls) == 1  # only the edited chunk is embedded
    skipped = {c.chunk_id: c for c in store.skipped}
    assert set(skipped) == {"T-001#summary", "T-001#section-0", "T-001#section-2"}
    assert skipped["T-001#section-2"].position == 1  # was 3
    assert skipped["T-001#section-0"].position == 2  # was 1
    assert skipped["T-001#section-0"].heading == "Section 0"


def test_failure_report_keeps_every_request_sent(s768, tmp_path):
    docs = make_corpus(tmp_path, 14)
    store, client = FakeStore(), FakeEmbedder(fail_at=9)
    assert run(s768, docs, store, client, tmp_path) == 1
    report = json.loads(next((tmp_path / "reports").glob("ingest_*.json")).read_text())
    assert len(report["chunks"]) == len(client.calls) == 10
    assert [c["ok"] for c in report["chunks"]] == [True] * 9 + [False]
    assert all(c["error_class"] is None for c in report["chunks"][:9])
    assert report["chunks"][9]["error_class"] == "PolicyBlockedError"
    assert report["n_errors"] == 1 and report["n_embedded"] == 0
