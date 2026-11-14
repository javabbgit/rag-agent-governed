import json
from pathlib import Path

import pytest

import eval_rerank_sweep as sweep
from rag_agent import evalset
from rag_agent.rerank import RerankError
from rag_agent.retrieve import Hit

FIX = Path(__file__).parent / "fixtures" / "evalset"
SECRET = "SECRET BODY TEXT"
RANKING = [
    "POL-003#ratio-limits",
    "POL-009#lump-sum-limit",
    "PRD-001#term",
    "PRD-001#eligibility",
]


def hit(cid, rank, score):
    return Hit(cid, cid.split("#")[0], "H", "current", SECRET, rank, score, None, None, None, None)


class Env:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.embeds = 0
        self.rerank_calls: list[int] = []
        self.preflight_error: Exception | None = None
        self.score_for = lambda n, cid, rank: 1.0 / rank  # identical across N
        self.latency_ms = 1.0

        def fake_embed(query, *, client=None):
            self.embeds += 1
            return [0.1, 0.2, 0.3, 0.4]

        def fake_retrieve(query, mode="hybrid", k=10, rerank_n=None, **kw):
            if mode == "hybrid":
                assert k == 50 and kw["query_vector"] == [0.1, 0.2, 0.3, 0.4]
                return [hit(c, i, 0.1) for i, c in enumerate(RANKING, start=1)]
            assert mode == "rerank" and k == 10
            self.rerank_calls.append(rerank_n)
            return [hit(c, i, self.score_for(rerank_n, c, i)) for i, c in enumerate(RANKING, 1)]

        def fake_info(url, timeout):
            if self.preflight_error:
                raise self.preflight_error
            return {
                "model_id": "m",
                "model_sha": "sha1",
                "version": "1.2",
                "max_client_batch_size": 64,
            }

        ok = evalset.Result([], {s: dict.fromkeys(evalset.KINDS, 0) for s in evalset.SPLITS})
        monkeypatch.setattr(sweep, "embed_query", fake_embed)
        monkeypatch.setattr(sweep, "retrieve", fake_retrieve)
        monkeypatch.setattr(sweep, "reranker_info", fake_info)
        monkeypatch.setattr(sweep, "check_pin", lambda info, rev: None)
        monkeypatch.setattr(sweep.check_evalset, "run_check", lambda *a, **k: ok)
        monkeypatch.setattr(sweep, "git_info", lambda: ("abc123", False))

    def run(self, settings, split="dev"):
        return sweep.run_sweep(
            settings,
            split,
            questions_path=FIX / "runner_questions.toml",
            docs_dir=FIX / "docs",
            manifest_path=Path("unused"),
            reports_dir=self.tmp / "reports",
        )


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def rows(mrrs, p95s):
    return [
        {"n": n, "mrr10": m, "p95_ms": p}
        for n, m, p in zip((10, 20, 30, 50), mrrs, p95s, strict=True)
    ]


def test_selection_drops_slow_n_then_takes_smallest_within_tolerance(capsys):
    table = rows([0.800, 0.830, 0.835, 0.830], [900, 1600, 2400, 6200])
    assert sweep.select_n(table) == 20
    out = capsys.readouterr().out
    assert "drop N=50" in out and "m* = 0.8350" in out and "select N=20" in out


def test_selection_none_eligible(capsys):
    assert sweep.select_n(rows([0.8, 0.8, 0.8, 0.8], [6000] * 4)) is None
    assert "no eligible N" in capsys.readouterr().out


def test_test_split_exits_2_before_anything(env, settings, capsys):
    assert env.run(settings, split="test") == 2
    assert sweep.main(["--split", "test"]) == 2
    assert env.embeds == 0 and not (env.tmp / "reports").exists()


def test_preflight_failure_exits_1_with_no_embed(env, settings):
    env.preflight_error = RerankError("down")
    assert env.run(settings) == 1
    assert env.embeds == 0 and not (env.tmp / "reports").exists()


def test_full_run_writes_reports_without_text(env, settings, capsys):
    assert env.run(settings) == 0
    assert env.embeds == 3  # one embedding per question, not per N
    assert env.rerank_calls == [10, 20, 30, 50] * 3
    out = capsys.readouterr().out
    assert "WARN" not in out and "selected N = 10" in out
    raw = next((env.tmp / "reports").glob("rerank_sweep_dev_*.json")).read_text()
    assert SECRET not in raw
    report = json.loads(raw)
    assert report["selected_n"] == 10 and [r["n"] for r in report["rows"]] == [10, 20, 30, 50]
    assert report["rows"][0]["cand_recall"] == 1.0
    md = next((env.tmp / "reports").glob("rerank_sweep_dev_*.md")).read_text()
    assert "selected N = 10" in md and "| N | cand_recall |" in md


def test_warns_when_a_chunk_score_differs_across_n(env, settings, capsys):
    env.score_for = lambda n, cid, rank: 1.0 / rank - (0.001 if n == 50 else 0.0)
    assert env.run(settings) == 0
    assert "WARN" in capsys.readouterr().out
    report = json.loads(next((env.tmp / "reports").glob("rerank_sweep_dev_*.json")).read_text())
    assert report["score_warnings"]


def test_no_eligible_n_exits_1(env, settings, monkeypatch, capsys):
    monkeypatch.setattr(sweep, "percentile", lambda xs, p: 9999.0)
    assert env.run(settings) == 1
    assert "no eligible N" in capsys.readouterr().out
