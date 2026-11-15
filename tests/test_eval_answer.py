import csv
import dataclasses
import json
from pathlib import Path

import pytest

import answer_review_tally as tally
import eval_answer as ea
from answer_fakes import FakeClient, hit, make_runtime
from rag_agent import answer as ans
from rag_agent import evalset
from rag_agent.errors import UpstreamServerError
from rag_agent.rerank import RerankError

FIX = Path(__file__).parent / "fixtures" / "evalset"
REPLY = "The ratio limit is 42 percent [S1]."


class Retriever:
    """D01 and D02 pass the gate; the out-of-scope question D31 does not."""

    def __init__(self):
        self.calls = []

    def __call__(self, query, mode="hybrid", k=10, **kw):
        self.calls.append((query, mode))
        if "vehicle" in query:
            return [hit(1, "FAQ-001#early-renewal", 0.01)]
        return [hit(1, "POL-003#ratio-limits", 0.9), hit(2, "PRD-001#term", 0.5)]


class Env:
    def __init__(self, tmp_path, monkeypatch, settings, chat=REPLY):
        self.tmp = tmp_path
        self.settings = settings
        self.client = FakeClient(chat=chat)
        self.retriever = Retriever()
        self.rt = make_runtime(settings, self.client, self.retriever)
        self.dirty = False
        self.reranker_problem = None
        ok = evalset.Result([], {s: dict.fromkeys(evalset.KINDS, 0) for s in evalset.SPLITS})
        monkeypatch.setattr(ea.check_evalset, "run_check", lambda *a, **k: ok)
        monkeypatch.setattr(ea, "git_info", lambda: ("abc123", self.dirty))
        monkeypatch.setattr(ea, "check_reranker", lambda s: self.reranker_problem)

    def run(self, split="dev", repeats=2, rt="default", **kw):
        return ea.run_eval(
            self.settings,
            split,
            repeats,
            rt=self.rt if rt == "default" else rt,
            questions_path=FIX / "runner_questions.toml",
            docs_dir=FIX / "docs",
            manifest_path=Path("unused"),
            reports_dir=self.tmp / "reports",
            **kw,
        )

    def report(self):
        (path,) = (self.tmp / "reports").glob("answer_dev_*.json")
        return json.loads(path.read_text())


@pytest.fixture
def env(tmp_path, monkeypatch, settings):
    return Env(tmp_path, monkeypatch, settings)


def test_test_split_needs_final(capsys):
    assert ea.main(["--split", "test"]) == 2
    assert "--final" in capsys.readouterr().out


def test_repeats_zero_runs_the_gate_only(env):
    assert env.run(repeats=0) == 0
    assert env.client.chats == []
    rep = env.report()
    assert rep["n_chat_calls"] == 0 and rep["n_embed"] == 3 and rep["complete"] is True
    assert rep["gate_summary"]["passed"]["ids"] == ["D01", "D02"]
    assert rep["gate_summary"]["low_score"]["ids"] == ["D31"]
    assert not list((env.tmp / "reports").glob("answer_review_*.csv"))


def test_full_run_report(env):
    assert env.run(repeats=2) == 0
    assert len(env.client.chats) == 4 and len(env.client.embeds) == 3  # D31 never generates
    rep = env.report()
    assert rep["n_chat_calls"] == 4 and rep["repeats"] == 2 and rep["cost_usd"] == 2.0
    assert rep["model_names"] == ["upstream/model"] and rep["prompt_sha256"] == "0" * 64
    assert rep["commit"] == "abc123" and rep["context_k"] == 5 and rep["threshold"] == 0.0621
    s = rep["metrics"]["summary"]
    assert s["correct_fallback"] == {"num": 2, "den": 2, "rate": 1.0}
    assert s["false_fallback"] == {"num": 0, "den": 4, "rate": 0.0}
    assert s["cited_gold"] == {"num": 2, "den": 4, "rate": 0.5}  # D02 cites a non-gold chunk
    assert s["answer_span_hit"] == {"num": 2, "den": 4, "rate": 0.5}
    assert s["consistent"] == {"num": 3, "den": 3, "rate": 1.0}
    assert s["answer_span_hit_all_repeats"] == {"num": 1, "den": 2, "rate": 0.5}
    assert s["model_declined"]["den"] == 4
    assert len(rep["metrics"]["per_repeat"]) == 2
    first = next(i for i in rep["items"] if i["qid"] == "D01" and i["repeat"] == 1)
    assert first["cited"] == [{"marker": "S1", "chunk_id": "POL-003#ratio-limits", "label": "gold"}]
    assert first["answer_span_hit"] is True and first["answer_text"] == REPLY
    assert (env.tmp / "reports").glob("answer_dev_*.md")
    with next((env.tmp / "reports").glob("answer_review_dev_*.csv")).open() as fh:
        rows = list(csv.DictReader(fh))
    assert [r["qid"] for r in rows] == ["D01", "D02"]
    assert rows[0]["markers"] == "S1" and rows[0]["verdict"] == ""
    assert rows[0]["cited_chunk_ids"] == "POL-003#ratio-limits"


def test_report_records_the_real_prompt_sha(env):
    _, sha = ans.load_prompt(env.settings.answer_prompt)
    env.rt = dataclasses.replace(env.rt, prompt_sha256=sha)
    assert env.run(repeats=1) == 0
    assert env.report()["prompt_sha256"] == sha


def test_error_does_not_stop_the_run(tmp_path, monkeypatch, settings):
    env = Env(tmp_path, monkeypatch, settings, chat=UpstreamServerError("boom"))
    assert env.run(repeats=1) == 1
    rep = env.report()
    assert rep["complete"] is False and rep["n_errors"] == 2
    assert {i["status"] for i in rep["items"]} == {"error", "fallback"}


def test_reranker_down_stops_before_any_request(env, capsys):
    env.reranker_problem = "reranker_unavailable"
    assert env.run() == 1
    assert env.client.embeds == [] and env.client.chats == []
    assert not (env.tmp / "reports").exists()


def test_reranker_failing_mid_run_writes_no_report(env):
    def boom(query, mode="hybrid", k=10, **kw):
        raise RerankError("down")

    env.rt.retrieve_fn = boom
    assert env.run() == 1
    assert not (env.tmp / "reports").exists()


def test_missing_threshold_stops_before_any_request(env, tmp_path):
    code = env.run(rt=None, threshold_path=tmp_path / "absent.toml")
    assert code == 1 and not (env.tmp / "reports").exists()


def test_dirty_tree_refuses_test_split(env):
    env.dirty = True
    assert env.run(split="test") == 2
    assert env.client.embeds == []


def test_span_normalisation():
    assert ea.span_hit("$30,000", "the limit is $30000 [S1]")
    assert ea.span_hit("18%", "Up to 18 % [S1].")
    assert ea.span_hit("42 percent", "It is  42   Percent [S2]")
    assert not ea.span_hit("42 percent", "It is 43 percent [S1]")
    assert not ea.span_hit("", "anything")


def _item(qid, text="The first limit applies to all files [S1]. The second one applies too [S2]."):
    return {
        "qid": qid,
        "repeat": 1,
        "kind": "answerable",
        "status": "answered",
        "answer_text": text,
        "context": [["S1", "A#a"], ["S2", "B#b"]],
    }


@pytest.mark.parametrize(("n", "expected"), [(5, 5), (10, 10), (25, 10), (30, 10), (3, 3)])
def test_review_sample_size(n, expected):
    items = [_item(f"D{i:02d}") for i in range(n)]
    rows = ea.review_rows(items)
    assert len({r["qid"] for r in rows}) == expected
    assert len(rows) == 2 * expected


def test_review_sample_takes_every_sth_question():
    items = [_item(f"D{i:02d}") for i in reversed(range(25))]  # unsorted input
    rows = ea.review_rows(items)
    qids = sorted({r["qid"] for r in rows})
    assert qids == [f"D{i:02d}" for i in range(0, 20, 2)]  # s = 2


def test_review_sample_skips_other_repeats_and_unanswered():
    other = {**_item("D01"), "repeat": 2}
    declined = {**_item("D02"), "status": "fallback"}
    oos = {**_item("D03"), "kind": "oos"}
    assert ea.review_rows([other, declined, oos]) == []


def test_review_rows_map_markers_to_chunks():
    (row, _) = ea.review_rows([_item("D01")])
    assert row["sentence_no"] == 1 and row["markers"] == "S1" and row["cited_chunk_ids"] == "A#a"


# --- tally -----------------------------------------------------------------------------------


def write_sheet(path, verdicts):
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(ea.REVIEW_FIELDS)
        for n, (qid, verdict) in enumerate(verdicts, start=1):
            w.writerow([qid, n, "s", "S1", "A#a", verdict])
    return str(path)


def test_tally_counts(tmp_path, capsys):
    f = write_sheet(
        tmp_path / "s.csv",
        [("D01", "supported"), ("D01", "partial"), ("D02", "unsupported"), ("D02", "Supported")],
    )
    assert tally.main([f]) == 0
    out = capsys.readouterr().out
    assert "sentences 4: supported 2, partial 1, unsupported 1" in out
    assert "questions with any unsupported sentence: 1 of 2 ['D02']" in out


@pytest.mark.parametrize("bad", ["", "maybe", "n/a"])
def test_tally_rejects_empty_or_unknown_verdict(tmp_path, bad):
    f = write_sheet(tmp_path / "s.csv", [("D01", "supported"), ("D02", bad)])
    assert tally.main([f]) == 1


def test_tally_rejects_empty_or_missing_file(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text(",".join(ea.REVIEW_FIELDS) + "\n")
    assert tally.main([str(p)]) == 1
    assert tally.main([str(tmp_path / "absent.csv")]) == 1
