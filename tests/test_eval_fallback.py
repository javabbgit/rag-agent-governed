import csv
import json
import tomllib

import pytest

import eval_fallback as fb

# (id, kind, category, rerank top-1, vector top-1, gold in top 10)
QUESTIONS = [
    ("D01", "answerable", "exact", 0.95, 0.80, True),
    ("D02", "answerable", "exact", 0.80, 0.70, True),
    ("D03", "answerable", "table", 0.40, 0.60, False),
    ("D31", "oos", "oos_far", 0.30, 0.20, False),
    ("D32", "oos", "oos_near", 0.50, 0.65, False),
]


def make_report(split="dev", rerank_n=20, dirty=False, commit="abc123", modes=None):
    qs = {}
    for qid, kind, cat, rr, vec, gold in QUESTIONS:
        mode_data = {
            "rerank": {"top1_score": rr, "first_gold_rank": 1 if gold else None},
            "vector": {"top1_score": vec, "first_gold_rank": 1 if gold else None},
        }
        qs[qid] = {"kind": kind, "category": cat, "modes": mode_data}
    return {
        "split": split,
        "modes": modes or ["vector", "rerank"],
        "rerank_n": rerank_n,
        "reranker": {"model_id": "m", "model_sha": "sha1", "version": "1"},
        "git_commit": commit,
        "git_dirty": dirty,
        "questions_sha256": "qsha",
        "fingerprint": "fp1",
        "questions": qs,
    }


class Env:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.threshold = tmp_path / "threshold.toml"
        self.threshold.write_text("# template, nothing set\n")
        self.ledger = tmp_path / "test_runs.csv"
        self.ledger.write_text(",".join(fb.LEDGER_HEADER) + "\n")
        self.committed_text: str | None = None
        self.commits_checked: list[str] = []

        def fake_committed(commit):
            self.commits_checked.append(commit)
            return self.committed_text

        monkeypatch.setattr(fb, "committed_threshold", fake_committed)

    def report(self, **kw):
        path = self.tmp / "retrieval_report.json"
        path.write_text(json.dumps(make_report(**kw)))
        return path

    def run(self, split, report, write=False):
        return fb.run_fallback(
            split,
            report,
            write_threshold=write,
            threshold_path=self.threshold,
            test_runs_path=self.ledger,
            reports_dir=self.tmp / "out",
        )


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def freeze(env):
    assert env.run("dev", env.report(), write=True) == 0
    env.threshold_text = env.threshold.read_text()
    env.committed_text = env.threshold_text  # what git show returns for the report's commit


def test_dev_writes_threshold_once_and_refuses_to_overwrite(env, capsys):
    freeze(env)
    data = tomllib.loads(env.threshold.read_text())
    assert data["signal"] == "rerank_top1" and data["rule"] == "youden-midpoint-ties-high"
    assert data["threshold"] == pytest.approx(0.65)  # between 0.50 (oos) and 0.80 (answerable)
    assert (data["interval_lo"], data["interval_hi"]) == (0.5, 0.8)
    assert data["rerank_n"] == 20 and data["reranker_model_sha"] == "sha1"
    assert data["dev_questions_sha256"] == "qsha" and data["dev_report_fingerprint"] == "fp1"
    assert data["dev_correct_fallback_rate"] == 1.0
    assert data["dev_false_fallback_rate"] == pytest.approx(1 / 3)
    assert env.run("dev", env.report(), write=True) == 2
    assert env.threshold.read_text() == env.threshold_text
    assert "already set" in capsys.readouterr().out


def test_dev_without_write_flag_touches_nothing_but_reports(env, capsys):
    assert env.run("dev", env.report()) == 0
    assert env.threshold.read_text() == "# template, nothing set\n"
    out = capsys.readouterr().out
    assert "AUROC rerank_top1" in out and "AUROC vector_top1" in out
    j = json.loads(next((env.tmp / "out").glob("fallback_dev_*.json")).read_text())
    assert j["rates"]["correct_fallback"]["num"] == 2 and j["rates"]["correct_fallback"]["den"] == 2
    assert j["rates"]["correct_by_category"]["oos_far"]["num"] == 1
    assert j["rates"]["false_by_category"]["table"]["num"] == 1
    assert j["rates"]["passed_without_gold"] == 0
    assert "oos_near" in next((env.tmp / "out").glob("fallback_dev_*.md")).read_text()


def test_dev_needs_rerank_mode(env):
    assert env.run("dev", env.report(modes=["vector"])) == 2


def test_test_refuses_dirty_report(env):
    freeze(env)
    assert env.run("test", env.report(split="test", dirty=True)) == 2
    assert env.run("test", env.report(split="test", dirty=None)) == 2
    assert env.ledger.read_text().count("\n") == 1


def test_test_refuses_commit_without_threshold(env):
    freeze(env)
    env.committed_text = None
    assert env.run("test", env.report(split="test")) == 2
    assert env.commits_checked == ["abc123"]
    assert env.ledger.read_text().count("\n") == 1


def test_test_reads_threshold_from_report_commit_not_working_tree(env):
    freeze(env)
    env.committed_text = "# template, nothing set\n"  # committed copy has no threshold
    env.threshold.write_text('signal = "rerank_top1"\nthreshold = 0.1\nrerank_n = 20\n')
    assert env.run("test", env.report(split="test")) == 2
    assert env.ledger.read_text().count("\n") == 1


def test_test_refuses_split_and_rerank_n_mismatch(env):
    freeze(env)
    assert env.run("test", env.report(split="dev")) == 2
    assert env.run("test", env.report(split="test", rerank_n=30)) == 2
    assert env.ledger.read_text().count("\n") == 1


def test_test_refuses_when_no_threshold_is_set(env):
    assert env.run("test", env.report(split="test")) == 2


def test_test_applies_committed_threshold_and_appends_one_line(env, capsys):
    freeze(env)
    before = env.ledger.read_text()
    assert env.run("test", env.report(split="test")) == 0
    lines = env.ledger.read_text().splitlines()
    assert env.ledger.read_text().startswith(before) and len(lines) == 2
    row = next(csv.DictReader(lines))
    assert row["report_file"] == "retrieval_report.json" and row["report_fingerprint"] == "fp1"
    assert row["questions_sha256"] == "qsha" and float(row["threshold"]) == pytest.approx(0.65)
    assert (row["n_oos"], row["n_oos_fallback"]) == ("2", "2")
    assert (row["n_answerable"], row["n_answerable_fallback"]) == ("3", "1")
    assert (row["correct_rate"], row["false_rate"]) == ("1.0000", "0.3333")
    assert "reference only, not used" in capsys.readouterr().out
    assert env.threshold.read_text() == env.threshold_text
    assert env.run("test", env.report(split="test")) == 0
    assert len(env.ledger.read_text().splitlines()) == 3


def test_outputs_hold_no_chunk_text(env):
    freeze(env)
    env.run("test", env.report(split="test"))
    for path in (env.tmp / "out").iterdir():
        assert "SECRET" not in path.read_text()
