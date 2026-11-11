"""Unit tests for scripts/search.py and scripts/retrieval_smoke.py (no network, no database)."""

import json

import pytest

import retrieval_smoke
import search
from fake_embed import FakeEmbedder
from rag_agent.config import Settings
from rag_agent.retrieve import Hit


def hit(chunk_id, rank=1, doc_id=None):
    return Hit(
        chunk_id=chunk_id,
        doc_id=doc_id or chunk_id.split("#")[0],
        heading="Heading",
        status="current",
        text="SECRET BODY TEXT",
        rank=rank,
        score=0.5,
        bm25_rank=1,
        bm25_score=1.5,
        vector_rank=None,
        vector_score=None,
    )


def test_smoke_queries_table():
    assert len(retrieval_smoke.SMOKE_QUERIES) == 12
    assert [q[0] for q in retrieval_smoke.SMOKE_QUERIES] == [f"Q{i:02d}" for i in range(1, 13)]
    assert retrieval_smoke.SMOKE_QUERIES[1][2] == ["POL-009", "PRD-001", "FAQ-002"]
    assert all(q[1] and q[2] for q in retrieval_smoke.SMOKE_QUERIES)


def test_first_expected_rank():
    f = retrieval_smoke.first_expected_rank
    assert f(["A", "B", "C"], ["C", "B"]) == 2
    assert f(["A"], ["B"]) is None and f([], ["B"]) is None


def test_fingerprint_is_order_independent_for_keys_but_not_ids():
    f = retrieval_smoke.fingerprint
    a = {"Q01": {"bm25": ["x", "y"], "vector": ["z"]}}
    b = {"Q01": {"vector": ["z"], "bm25": ["x", "y"]}}
    assert f(a) == f(b) and len(f(a)) == 64
    assert f(a) != f({"Q01": {"bm25": ["y", "x"], "vector": ["z"]}})


def fake_retrieve(expected_in_top5=True):
    calls = []

    def fn(query, mode, k, collection, include_superseded, query_vector=None, **kw):
        calls.append((mode, query_vector is not None, kw["dsn"], kw["client"] is not None))
        docs = [("POL-003" if expected_in_top5 else "ZZZ-999"), "FAQ-001"]
        return [hit(f"{d}#s", i, d) for i, d in enumerate(docs, start=1)]

    fn.calls = calls
    return fn


def test_run_smoke_report_and_exit_zero_even_with_misses(settings: Settings, tmp_path, capsys):
    emb = FakeEmbedder()
    fn = fake_retrieve(expected_in_top5=False)
    code = retrieval_smoke.run_smoke(
        settings, "main", client=emb, retrieve_fn=fn, reports_dir=tmp_path
    )
    assert code == 0
    assert len(emb.calls) == 12  # one embedding per query
    assert all(c[1] and c[2] == settings.database_url for c in fn.calls) and len(fn.calls) == 36
    out = capsys.readouterr().out
    assert "bm25 0/12 · vector 0/12 · hybrid 0/12" in out
    (path,) = tmp_path.glob("smoke_*.json")
    rep = json.loads(path.read_text())
    assert rep["n_errors"] == 0 and rep["hits_at_5"] == {"bm25": 0, "vector": 0, "hybrid": 0}
    assert rep["EMBED_MODEL"] == settings.embed_model and rep["collection"] == "main"
    assert len(rep["fingerprint"]) == 64
    assert rep["queries"]["Q01"]["bm25"] == {
        "top10": ["ZZZ-999#s", "FAQ-001#s"],
        "first_expected_rank": None,
    }
    raw = path.read_text()
    assert "SECRET BODY TEXT" not in raw and settings.openai_api_key not in raw


def test_run_smoke_counts_hits(settings, tmp_path, capsys):
    retrieval_smoke.run_smoke(
        settings, client=FakeEmbedder(), retrieve_fn=fake_retrieve(True), reports_dir=tmp_path
    )
    # only Q01 expects POL-003
    assert "bm25 1/12 · vector 1/12 · hybrid 1/12" in capsys.readouterr().out


def test_run_smoke_exit_one_on_error(settings, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("db down")

    code = retrieval_smoke.run_smoke(
        settings, client=FakeEmbedder(), retrieve_fn=boom, reports_dir=tmp_path
    )
    assert code == 1
    (path,) = tmp_path.glob("smoke_*.json")
    assert json.loads(path.read_text())["n_errors"] == 36


def test_run_smoke_exit_one_on_embed_error(settings, tmp_path):
    code = retrieval_smoke.run_smoke(
        settings,
        client=FakeEmbedder(fail_at=0),
        retrieve_fn=fake_retrieve(),
        reports_dir=tmp_path,
    )
    assert code == 1


@pytest.fixture
def patched_settings(monkeypatch, settings):
    seen = []

    def load(env_file=None):
        seen.append(env_file)
        return settings

    monkeypatch.setattr(search, "load_settings", load)
    return seen, settings


def test_search_passes_settings_dsn_and_client(patched_settings, capsys):
    seen, settings = patched_settings
    got = {}

    def fn(query, mode, k, collection, include_superseded, **kw):
        got.update(query=query, mode=mode, k=k, collection=collection, kw=kw)
        return [hit("POL-003#a")]

    emb = FakeEmbedder()
    code = search.main(["ratio", "-k", "3", "--env-file", "x.env"], retrieve_fn=fn, client=emb)
    assert code == 0 and seen == ["x.env"]
    assert got["k"] == 3 and got["mode"] == "hybrid" and got["collection"] == "main"
    assert got["kw"] == {"client": emb, "dsn": settings.database_url}
    assert "POL-003#a" in capsys.readouterr().out


def test_search_bm25_builds_no_client(patched_settings, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no client in bm25 mode")

    monkeypatch.setattr(search, "LLMClient", boom)
    got = {}

    def fn(*a, **kw):
        got.update(kw)
        return []

    assert search.main(["ratio", "--mode", "bm25"], retrieve_fn=fn) == 0
    assert got["client"] is None


def test_search_json_has_no_text(patched_settings, capsys):
    code = search.main(["ratio", "--json"], retrieve_fn=lambda *a, **k: [hit("POL-003#a")])
    assert code == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["chunk_id"] == "POL-003#a" and "text" not in rows[0]


def test_search_error_exits_one(patched_settings, capsys):
    def boom(*a, **k):
        raise RuntimeError("db down")

    assert search.main(["ratio", "--mode", "bm25"], retrieve_fn=boom) == 1
    assert "RuntimeError" in capsys.readouterr().out
