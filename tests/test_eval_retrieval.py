import csv
import json
from pathlib import Path

import pytest

import eval_retrieval
from rag_agent import evalset
from rag_agent.retrieve import Hit

FIX = Path(__file__).parent / "fixtures" / "evalset"
SECRET = "SECRET BODY TEXT"
OLD_ROW = "2026-01-01T00:00:00.000Z,old,False,x,1,bm25,1.0000,1.0000,1.0000,1.0000,1.0000,1.0000"

# query -> ranked (chunk_id, status); anything else gets a default ranking
RANKINGS = {
    "What is the maximum TDS ratio?": [
        ("POL-003#ratio-limits", "current"),
        ("POL-003-V1#former-ratio-limits", "superseded"),
        ("POL-009#lump-sum-limit", "current"),
        ("PRD-001#term", "current"),
        ("PRD-001#eligibility", "current"),
    ],
}
DEFAULT = [
    ("PRD-001#term", "current"),
    ("POL-009#unused-privileges", "current"),
    ("POL-009#lump-sum-limit", "current"),
    ("POL-003#ratio-limits", "current"),
]


def make_hits(query, mode, k=10):
    rows = RANKINGS.get(query, DEFAULT)
    if mode == "vector":
        rows = list(reversed(rows))
    hits = []
    for i, (cid, status) in enumerate(rows[:k], start=1):
        hits.append(
            Hit(
                chunk_id=cid,
                doc_id=cid.split("#")[0],
                heading="Heading " + cid.split("#")[1],
                status=status,
                text=SECRET,
                rank=i,
                score=1.0 / i,
                bm25_rank=None,
                bm25_score=None,
                vector_rank=None,
                vector_score=None,
            )
        )
    return hits


class Env:
    def __init__(self, tmp_path, monkeypatch, questions_text=None):
        self.q_path = tmp_path / "questions.toml"
        self.q_path.write_text(
            questions_text or (FIX / "runner_questions.toml").read_text(), encoding="utf-8"
        )
        self.test_runs = tmp_path / "test_runs.csv"
        header = ",".join(eval_retrieval.TEST_RUNS_HEADER)
        self.test_runs.write_text(header + "\n" + OLD_ROW + "\n", encoding="utf-8")
        self.initial = self.test_runs.read_text()
        self.tmp = tmp_path
        self.embeds: list[str] = []
        self.retrieves: list[tuple[str, str]] = []
        self.fail_on_retrieve_call: int | None = None
        self.dirty = False

        def fake_embed(query, *, client=None):
            self.embeds.append(query)
            return [0.1, 0.2, 0.3, 0.4]

        def fake_retrieve(
            query,
            mode="hybrid",
            k=10,
            collection="main",
            include_superseded=True,
            query_vector=None,
            **kw,
        ):
            n = len(self.retrieves)
            self.retrieves.append((query, mode))
            if self.fail_on_retrieve_call is not None and n == self.fail_on_retrieve_call:
                raise RuntimeError("boom")
            assert collection == "main" and include_superseded is True and k == 10
            if mode in ("vector", "hybrid"):
                assert query_vector == [0.1, 0.2, 0.3, 0.4]
            return make_hits(query, mode, k)

        ok = evalset.Result([], {s: dict.fromkeys(evalset.KINDS, 0) for s in evalset.SPLITS})
        monkeypatch.setattr(eval_retrieval, "embed_query", fake_embed)
        monkeypatch.setattr(eval_retrieval, "retrieve", fake_retrieve)
        monkeypatch.setattr(eval_retrieval.check_evalset, "run_check", lambda *a, **k: ok)
        monkeypatch.setattr(eval_retrieval, "git_info", lambda: ("abc123", self.dirty))

    def run(self, settings, split="dev", modes=("bm25", "vector", "hybrid"), reports=None, **kw):
        return eval_retrieval.run_eval(
            settings,
            split,
            list(modes),
            questions_path=self.q_path,
            docs_dir=FIX / "docs",
            manifest_path=Path("unused"),
            reports_dir=reports or self.tmp / "reports",
            test_runs_path=self.test_runs,
            **kw,
        )


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def test_one_embed_per_question_none_for_bm25_only(env, settings):
    assert env.run(settings) == 0
    assert len(env.embeds) == 3
    assert len(env.retrieves) == 9
    env.embeds.clear()
    assert env.run(settings, modes=("bm25",)) == 0
    assert env.embeds == []


def test_exception_at_question_three_writes_nothing(env, settings, capsys):
    env.fail_on_retrieve_call = 6  # third question, first mode
    code = env.run(settings, split="test")
    assert code == 1
    assert not (env.tmp / "reports").exists()
    assert env.test_runs.read_text() == env.initial
    assert "ok" not in capsys.readouterr().out


def test_embed_exception_stops_and_writes_nothing(env, settings, monkeypatch):
    def boom(query, *, client=None):
        raise RuntimeError("down")

    monkeypatch.setattr(eval_retrieval, "embed_query", boom)
    assert env.run(settings, split="test") == 1
    assert not (env.tmp / "reports").exists()
    assert env.test_runs.read_text() == env.initial


def test_dirty_tree_refuses_test_split(env, settings):
    env.dirty = True
    assert env.run(settings, split="test") == 2
    assert env.embeds == []
    assert env.test_runs.read_text() == env.initial


def test_dirty_tree_does_not_block_dev_or_pool(env, settings):
    env.dirty = True
    assert env.run(settings, split="dev") == 0
    assert env.run(settings, split="test", pool_only=True) == 0


def test_unreviewed_question_refuses_test_split(tmp_path, monkeypatch, settings):
    text = (FIX / "runner_questions.toml").read_text()
    head, _, tail = text.partition('id = "T02"')
    text = head + 'id = "T02"' + tail.replace("reviewed = true", "reviewed = false", 1)
    e = Env(tmp_path, monkeypatch, text)
    assert e.run(settings, split="test") == 2
    assert e.embeds == []


def test_test_run_appends_one_line_per_mode_and_keeps_old(env, settings):
    assert env.run(settings, split="test") == 0
    lines = env.test_runs.read_text().splitlines()
    assert lines[:2] == env.initial.splitlines()
    assert len(lines) == 2 + 3
    rows = list(csv.DictReader(lines[0:1] + lines[2:]))
    assert [r["mode"] for r in rows] == ["bm25", "vector", "hybrid"]
    assert all(r["n_queries"] == "3" and r["git_commit"] == "abc123" for r in rows)
    assert rows[0]["hit1"] == "0.0000"
    env.run(settings, split="test", reports=env.tmp / "reports2")
    assert len(env.test_runs.read_text().splitlines()) == 2 + 6


def test_dev_run_does_not_touch_test_runs(env, settings):
    assert env.run(settings, split="dev") == 0
    assert env.test_runs.read_text() == env.initial


def test_metrics_values(env, settings):
    assert env.run(settings, split="dev", modes=("bm25", "vector")) == 0
    report = json.loads(next((env.tmp / "reports").glob("retrieval_dev_*.json")).read_text())
    bm = report["metrics"]["modes"]["bm25"]
    # D01 gold at rank 1; D02 gold (lump-sum-limit) at rank 3 in the default ranking
    assert bm["hit1"] == 0.5 and bm["hit3"] == 1.0
    assert bm["mrr10"] == pytest.approx((1 + 1 / 3) / 2)
    assert bm["doc_hit1"] == 0.5
    assert bm["superseded_at1"] == 0
    vec = report["metrics"]["modes"]["vector"]
    assert vec["superseded_at1"] == 0
    assert report["n_answerable"] == 2 and report["n_oos"] == 1
    assert set(report["metrics"]["oos_top1_score"]["bm25"]) == {"D31"}
    assert report["metrics"]["paired"] == {}
    assert report["questions"]["D01"]["modes"]["bm25"]["first_gold_rank"] == 1


def test_paired_diffs_present_with_all_modes(env, settings):
    env.run(settings)
    report = json.loads(next((env.tmp / "reports").glob("retrieval_dev_*.json")).read_text())
    assert set(report["metrics"]["paired"]) == {"hybrid-bm25", "hybrid-vector"}
    md = next((env.tmp / "reports").glob("retrieval_dev_*.md")).read_text()
    assert "MRR@10 [95% CI]" in md and "hybrid-bm25" in md


def test_pool_only_has_no_metrics_and_skips_judged_and_superseded(env, settings, capsys):
    assert env.run(settings, pool_only=True, pool_depth=5) == 0
    out = capsys.readouterr().out
    assert "Hit" not in out and "MRR" not in out
    assert "n_queries 3" in out and "started_utc" in out and "finished_utc" in out
    path = next((env.tmp / "reports").glob("pool_dev_*.csv"))
    text = path.read_text()
    assert "Hit" not in text and "MRR" not in text
    assert not list((env.tmp / "reports").glob("retrieval_*"))
    rows = list(csv.DictReader(text.splitlines()))
    d01 = {r["chunk_id"] for r in rows if r["qid"] == "D01"}
    assert "POL-003#ratio-limits" not in d01  # gold
    assert "POL-003-V1#former-ratio-limits" not in d01  # superseded
    assert d01 == {"POL-009#lump-sum-limit", "PRD-001#term", "PRD-001#eligibility"}
    d02 = {r["chunk_id"] for r in rows if r["qid"] == "D02"}
    assert "POL-009#lump-sum-limit" not in d02  # gold
    assert all(r["qid"] != "D31" for r in rows)
    assert all(r["judgment"] == "" for r in rows)
    row = next(r for r in rows if r["qid"] == "D01" and r["chunk_id"] == "PRD-001#term")
    assert row["best_rank"] == "4" or row["best_rank"] == "2"
    assert "bm25:4" in row["ranks"] and "vector:2" in row["ranks"]


def test_fingerprint_identical_across_runs(env, settings):
    env.run(settings, reports=env.tmp / "r1")
    env.run(settings, reports=env.tmp / "r2")
    f1 = json.loads(next((env.tmp / "r1").glob("retrieval_dev_*.json")).read_text())
    f2 = json.loads(next((env.tmp / "r2").glob("retrieval_dev_*.json")).read_text())
    assert f1["fingerprint"] == f2["fingerprint"]
    assert f1["questions_sha256"] == f2["questions_sha256"]


def _walk(value):
    yield value
    if isinstance(value, dict):
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


def test_report_json_has_no_text_vectors_or_keys(env, settings):
    env.run(settings)
    raw = next((env.tmp / "reports").glob("retrieval_dev_*.json")).read_text()
    assert SECRET not in raw
    assert "sk-test" not in raw
    report = json.loads(raw)
    for node in _walk(report):
        if isinstance(node, list):
            assert node != [0.1, 0.2, 0.3, 0.4]
            assert sum(isinstance(x, float) for x in node) < 4
        if isinstance(node, str):
            assert "SECRET" not in node and "sk-test" not in node
    assert report["embed_base_host"] == "gateway.invalid"
    assert report["EMBED_MODEL"] == "rag-embed"
    for key in (
        "started_utc",
        "finished_utc",
        "split",
        "modes",
        "k",
        "collection",
        "include_superseded",
        "git_commit",
        "git_dirty",
        "questions_sha256",
        "n_queries",
        "fingerprint",
        "n_errors",
    ):
        assert key in report


def test_main_table_and_final_line(env, settings, capsys):
    env.run(settings)
    out = capsys.readouterr().out
    assert "| mode | Hit@1 | Hit@3 | Hit@5 | MRR@10 [95% CI] | doc Hit@1 | superseded@1 |" in out
    assert "eval dev: n_queries 3, fingerprint " in out and out.rstrip().endswith(", ok")


def test_check_failure_exits_before_any_request(env, settings, monkeypatch):
    bad = evalset.Result(
        [evalset.Finding("FAIL", "D01", "x")],
        {s: dict.fromkeys(evalset.KINDS, 0) for s in evalset.SPLITS},
    )
    monkeypatch.setattr(eval_retrieval.check_evalset, "run_check", lambda *a, **k: bad)
    assert env.run(settings) == 1
    assert env.embeds == [] and env.retrieves == []


def test_empty_split_prints_no_questions(tmp_path, monkeypatch, settings, capsys):
    e = Env(tmp_path, monkeypatch, "version = 1\n")
    assert e.run(settings) == 1
    assert "no questions" in capsys.readouterr().out


def test_untracked_file_refuses_but_ignored_file_does_not(tmp_path, monkeypatch):
    import subprocess

    from rag_agent import ingest

    repo = tmp_path / "repo"
    (repo / "reports").mkdir(parents=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    (repo / ".gitignore").write_text("reports/\n")
    subprocess.run([*git[:1], "init", "-q"], cwd=repo, check=True)
    subprocess.run([*git, "add", "."], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "x"], cwd=repo, check=True)
    monkeypatch.setattr(ingest, "REPO_ROOT", repo)
    assert eval_retrieval.tree_not_clean() is False
    (repo / "reports" / "out.json").write_text("{}")
    assert eval_retrieval.tree_not_clean() is False
    (repo / "new.txt").write_text("x")
    assert eval_retrieval.tree_not_clean() is True


def test_untracked_file_refuses_test_split(env, settings, monkeypatch):
    env.dirty = True  # git_info() reports dirty, as it does for an untracked file
    assert env.run(settings, split="test") == 2
    assert env.test_runs.read_text() == env.initial
