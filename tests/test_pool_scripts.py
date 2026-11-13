"""Unit tests for scripts/pool_sheet.py and scripts/apply_pool.py (no network, no database)."""

import csv
import re
import shutil
from pathlib import Path

import pytest

import apply_pool
import pool_sheet
from rag_agent import evalset

FIX = Path(__file__).parent / "fixtures" / "evalset"
DOCS = FIX / "docs"
REPO = Path(__file__).resolve().parent.parent
REAL_Q = REPO / "eval" / "retrieval" / "questions.toml"
MANIFEST = REPO / "corpus" / "manifest.toml"
HEADER = "# Retrieval eval questions. Format: see README.md.\n"
FIELDS = ["qid", "category", "query", "chunk_id", "heading", "best_rank", "ranks", "judgment"]

BASE = {
    "id": "D01",
    "split": "dev",
    "kind": "answerable",
    "category": "exact",
    "persona": "staff",
    "query": "What is the maximum TDS ratio?",
    "answer": "42 percent",
    "gold": ["POL-003#ratio-limits"],
    "related": ["POL-003#limits-table"],
    "nonrel": [],
    "origin": "smoke-Q01",
    "reviewed": True,
}
SECOND = {
    **BASE,
    "id": "D02",
    "query": "How much can I prepay as a lump sum?",
    "answer": "18 percent",
    "gold": ["POL-009#lump-sum-limit"],
    "related": [],
    "origin": "smoke-Q02",
}
OOS = {
    "id": "D31",
    "split": "dev",
    "kind": "oos",
    "category": "oos_far",
    "persona": "borrower",
    "query": "Can I get a loan to buy a used vehicle?",
    "answer": "",
    "gold": [],
    "related": [],
    "nonrel": [],
    "origin": "new",
    "reviewed": True,
    "topic": "car loans",
}
TEST_Q = {**BASE, "id": "T01", "split": "test", "origin": "new", "gold": ["POL-009#lump-sum-limit"]}


def make_questions(tmp_path: Path, qs=(BASE, SECOND, OOS, TEST_Q)) -> Path:
    path = tmp_path / "questions.toml"
    text = apply_pool.dump_questions({"version": 1, "q": [dict(q) for q in qs]}, HEADER)
    path.write_text(text, encoding="utf-8", newline="")
    return path


def write_csv(path: Path, rows, *, bom=False, crlf=False, fields=FIELDS):
    nl = "\r\n" if crlf else "\n"
    with open(path, "w", encoding="utf-8-sig" if bom else "utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator=nl)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    return path


def row(qid, chunk, judgment="", rank=1):
    return {
        "qid": qid,
        "category": "exact",
        "query": "q",
        "chunk_id": chunk,
        "heading": "h",
        "best_rank": str(rank),
        "ranks": f"hybrid:{rank}",
        "judgment": judgment,
    }


@pytest.fixture
def ok_check(monkeypatch):
    ok = evalset.Result([], {s: dict.fromkeys(evalset.KINDS, 0) for s in evalset.SPLITS})
    monkeypatch.setattr(apply_pool.check_evalset, "run_check", lambda *a, **k: ok)


def apply(tmp_path, qpath, rows, **kw):
    csv_path = write_csv(tmp_path / "judge.csv", rows, **kw)
    return apply_pool.run_apply(csv_path, qpath, DOCS, MANIFEST)


# --- serializer / round trip -------------------------------------------------------------------


def test_round_trip_real_file_is_byte_identical():
    raw = REAL_Q.read_bytes().decode("utf-8")
    data = evalset.load(REAL_Q)
    assert apply_pool.dump_questions(data, apply_pool.header_of(raw)).encode("utf-8") == (
        REAL_Q.read_bytes()
    )


def test_round_trip_helper_layout(tmp_path):
    p = make_questions(tmp_path)
    text = p.read_text(encoding="utf-8")
    assert text.startswith(HEADER + 'version = 1\n\n[[q]]\nid = "D01"\n')
    assert "nonrel = []\n" in text and 'gold = [\n  "POL-003#ratio-limits",\n]\n' in text
    assert apply_pool.dump_questions(evalset.load(p), apply_pool.header_of(text)) == text


def test_blank_judgments_leave_real_file_identical(tmp_path, capsys):
    q = tmp_path / "questions.toml"
    shutil.copyfile(REAL_Q, q)
    before = q.read_bytes()
    rows = [row("D01", "POL-004#summary"), row("D02", "POL-004#summary")]
    csv_path = write_csv(tmp_path / "judge.csv", rows)
    assert apply_pool.run_apply(csv_path, q, REPO / "corpus" / "docs", MANIFEST) == 0
    assert q.read_bytes() == before
    assert "unchanged" in capsys.readouterr().out


# --- apply: success ----------------------------------------------------------------------------


def test_apply_changes_only_expected_lists(tmp_path, ok_check, capsys):
    q = make_questions(tmp_path)
    before = evalset.load(q)
    rows = [
        row("D01", "PRD-001#term", "NonRel"),
        row("D01", "POL-009#lump-sum-limit", " RELATED "),
        row("D02", "POL-003#ratio-limits", "gold"),
        row("T01", "PRD-001#term", "nonrel"),
        row("D02", "PRD-001#eligibility", ""),
    ]
    assert apply(tmp_path, q, rows) == 0
    after = evalset.load(q)
    exp = {
        "D01": {
            "nonrel": ["PRD-001#term"],
            "related": ["POL-003#limits-table", "POL-009#lump-sum-limit"],
        },
        "D02": {"gold": ["POL-009#lump-sum-limit", "POL-003#ratio-limits"]},
        "T01": {"nonrel": ["PRD-001#term"]},
    }
    for b, a in zip(before["q"], after["q"], strict=True):
        want = {**b, **exp.get(b["id"], {})}
        assert a == want, b["id"]
    text = q.read_text(encoding="utf-8")
    assert apply_pool.dump_questions(after, HEADER) == text
    out = capsys.readouterr().out
    assert "gold 1, related 1, nonrel 2" in out
    assert "dev 3, test 1" in out


def test_apply_accepts_bom_and_crlf(tmp_path, ok_check):
    q = make_questions(tmp_path)
    rows = [row("D01", "PRD-001#term", "nonrel"), row("D02", "PRD-001#eligibility", "related")]
    assert apply(tmp_path, q, rows, bom=True, crlf=True) == 0
    data = evalset.load(q)
    assert data["q"][0]["nonrel"] == ["PRD-001#term"]
    assert data["q"][1]["related"] == ["PRD-001#eligibility"]
    assert "\r" not in q.read_text(encoding="utf-8")


# --- apply: refusals leave the file byte-identical ---------------------------------------------


@pytest.mark.parametrize(
    "rows,needle",
    [
        ([row("D99", "PRD-001#term", "gold")], "unknown qid"),
        ([row("D01", "PRD-001#nope", "gold")], "does not exist"),
        ([row("D01", "POL-003-V1#former-ratio-limits", "nonrel")], "superseded"),
        ([row("D01", "POL-003#ratio-limits", "related")], "already in gold"),
        ([row("D01", "POL-003#limits-table", "nonrel")], "already in related"),
        ([row("D01", "PRD-001#term", "maybe")], "not one of"),
        (
            [row("D01", "PRD-001#term", "gold"), row("D01", "PRD-001#term", "nonrel")],
            "already in gold",
        ),
        ([row("D01", "PRD-001#term", "nonrel"), row("D01", "BAD#x", "gold")], "does not exist"),
    ],
)
def test_refusals_keep_file_identical(tmp_path, ok_check, capsys, rows, needle):
    q = make_questions(tmp_path)
    before = q.read_bytes()
    assert apply(tmp_path, q, rows) == 1
    assert q.read_bytes() == before
    assert needle in capsys.readouterr().out
    assert not list(tmp_path.glob(".questions-*"))


def test_nonrel_already_present_refused(tmp_path, ok_check, capsys):
    q = make_questions(tmp_path, [{**BASE, "nonrel": ["PRD-001#term"]}])
    before = q.read_bytes()
    assert apply(tmp_path, q, [row("D01", "PRD-001#term", "gold")]) == 1
    assert q.read_bytes() == before
    assert "already in nonrel" in capsys.readouterr().out


def test_missing_column_refused(tmp_path, ok_check, capsys):
    q = make_questions(tmp_path)
    before = q.read_bytes()
    fields = [f for f in FIELDS if f != "judgment"]
    assert apply(tmp_path, q, [row("D01", "PRD-001#term")], fields=fields) == 1
    assert q.read_bytes() == before
    assert "missing column" in capsys.readouterr().out


def test_check_failure_refuses(tmp_path, monkeypatch, capsys):
    bad = evalset.Result(
        [evalset.Finding(evalset.FAIL, "D01", "boom")],
        {s: dict.fromkeys(evalset.KINDS, 0) for s in evalset.SPLITS},
    )
    monkeypatch.setattr(apply_pool.check_evalset, "run_check", lambda *a, **k: bad)
    q = make_questions(tmp_path)
    before = q.read_bytes()
    assert apply(tmp_path, q, [row("D01", "PRD-001#term", "nonrel")]) == 1
    assert q.read_bytes() == before
    assert "boom" in capsys.readouterr().out


def test_real_check_runs_on_candidate(tmp_path, capsys):
    """No monkeypatch: the incomplete fixture set fails the default-level count rules."""
    q = make_questions(tmp_path)
    before = q.read_bytes()
    assert apply(tmp_path, q, [row("D01", "PRD-001#term", "nonrel")]) == 1
    assert q.read_bytes() == before
    assert "count:" in capsys.readouterr().out


def test_layout_drift_refused(tmp_path, ok_check, capsys):
    q = make_questions(tmp_path)
    q.write_text(
        q.read_text(encoding="utf-8").replace(
            'gold = [\n  "POL-003#ratio-limits",\n]', 'gold = ["POL-003#ratio-limits"]'
        ),
        encoding="utf-8",
        newline="",
    )
    before = q.read_bytes()
    assert apply(tmp_path, q, [row("D01", "PRD-001#term", "nonrel")]) == 1
    assert q.read_bytes() == before
    assert "not reproducible" in capsys.readouterr().out


# --- pool_sheet --------------------------------------------------------------------------------


def run_sheet(tmp_path, rows, **kw):
    q = make_questions(tmp_path)
    pool = write_csv(tmp_path / "pool_dev_X.csv", rows, **kw)
    rc = pool_sheet.run_sheet(pool, q, DOCS)
    return rc, tmp_path / "pool_dev_X_judge.csv", tmp_path / "pool_dev_X_judge.md"


def test_sheet_columns_flag_sort_and_markdown(tmp_path):
    truth = evalset.build_truth(DOCS)
    yes = next(c for c, s in truth.chunks.items() if "42 percent" in s.text)
    no = next(c for c, s in truth.chunks.items() if "42 percent" not in s.text)
    rows = [
        row("D02", no, "gold", rank=1),
        row("D01", no, "gold", rank=1),
        row("D01", yes, "", rank=3),
        row("D02", yes, "", rank=2),
    ]
    rc, jcsv, jmd = run_sheet(tmp_path, rows)
    assert rc == 0
    with open(jcsv, encoding="utf-8", newline="") as fh:
        out = list(csv.DictReader(fh))
    assert list(out[0].keys()) == FIELDS + ["answer", "contains_answer"]
    assert [r["contains_answer"] for r in out] == sorted(
        (r["contains_answer"] for r in out), key=lambda v: v != "yes"
    )
    assert all(r["judgment"] == "" for r in out)
    assert out[0]["answer"] == "42 percent"
    md = jmd.read_text(encoding="utf-8")
    assert "42 percent" in md and "current gold: POL-003#ratio-limits" in md
    assert truth.chunks[yes].text.strip() in md
    assert len(re.findall(r"^## [DT]\d\d - ", md, re.M)) == len(rows)


def test_sheet_sort_order_exact(tmp_path):
    truth = evalset.build_truth(DOCS)
    yes = next(c for c, s in truth.chunks.items() if "42 percent" in s.text)
    others = [c for c in truth.chunks if c != yes and "42 percent" not in truth.chunks[c].text]
    rows = [row("D01", others[0], rank=1), row("D02", others[1], rank=1), row("D01", yes, rank=5)]
    rc, jcsv, _ = run_sheet(tmp_path, rows)
    assert rc == 0
    with open(jcsv, encoding="utf-8", newline="") as fh:
        got = [(r["qid"], r["chunk_id"]) for r in csv.DictReader(fh)]
    assert got == [("D01", yes), ("D01", others[0]), ("D02", others[1])]


def test_sheet_bom_crlf_and_refusals(tmp_path, capsys):
    rc, jcsv, _ = run_sheet(tmp_path, [row("D01", "POL-003#ratio-limits")], bom=True, crlf=True)
    assert rc == 0 and jcsv.read_bytes()[:3] != b"\xef\xbb\xbf" and b"\r" not in jcsv.read_bytes()
    jcsv.unlink()
    rc, jcsv2, _ = run_sheet(tmp_path, [row("D77", "POL-003#ratio-limits")])
    assert rc == 1 and not jcsv2.exists() and "unknown qid" in capsys.readouterr().out
    rc, jcsv3, _ = run_sheet(tmp_path, [row("D01", "POL-003#nope")])
    assert rc == 1 and not jcsv3.exists() and "unknown chunk_id" in capsys.readouterr().out
