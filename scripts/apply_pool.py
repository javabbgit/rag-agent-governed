"""Apply pool judgments (gold, related, nonrel) to eval/retrieval/questions.toml. No network, no DB.

Reads the judgment column of a judged pool CSV (blank = skip). Refuses with exit 1, leaving the
file byte-identical, on: an unknown qid, a missing or superseded chunk, a chunk already in any list
of that question, a bad judgment word, a layout the serializer cannot reproduce, or an
evalset.check FAIL at the default level on the result.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import check_evalset
from rag_agent import evalset

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUESTIONS = REPO_ROOT / "eval" / "retrieval" / "questions.toml"
DEFAULT_DOCS = REPO_ROOT / "corpus" / "docs"
DEFAULT_MANIFEST = REPO_ROOT / "corpus" / "manifest.toml"
JUDGMENTS = ("gold", "related", "nonrel")


def header_of(text: str) -> str:
    """The leading comment block of the questions file (kept verbatim)."""
    lines = []
    for line in text.split("\n"):
        if not line.startswith("#"):
            break
        lines.append(line)
    return "".join(f"{ln}\n" for ln in lines)


def _value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, list):
        if not v:
            return "[]"
        return "[\n" + "".join(f"  {json.dumps(x)},\n" for x in v) + "]"
    raise TypeError(f"cannot serialize {type(v).__name__}")


def dump_questions(data: dict[str, Any], header: str) -> str:
    out = [header, f"version = {data['version']}\n"]
    for q in data.get("q", []):
        out.append("\n[[q]]\n")
        for key, v in q.items():
            out.append(f"{key} = {_value(v)}\n")
    return "".join(out)


def read_judgments(path: Path) -> tuple[list[tuple[int, str, str, str]], list[str]]:
    """Rows of (line, qid, chunk_id, judgment) with a non-blank judgment, and errors."""
    errors: list[str] = []
    rows: list[tuple[int, str, str, str]] = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        reader.fieldnames = [f.strip() for f in (reader.fieldnames or [])]
        for col in ("qid", "chunk_id", "judgment"):
            if col not in reader.fieldnames:
                errors.append(f"missing column {col!r}")
        if errors:
            return rows, errors
        for row in reader:
            n = reader.line_num
            raw = (row.get("judgment") or "").strip().lower()
            if not raw:
                continue
            qid, cid = (row.get("qid") or "").strip(), (row.get("chunk_id") or "").strip()
            if raw not in JUDGMENTS:
                errors.append(f"line {n}: judgment {raw!r} is not one of {', '.join(JUDGMENTS)}")
                continue
            rows.append((n, qid, cid, raw))
    return rows, errors


def run_apply(
    csv_path: Path,
    questions_path: Path = DEFAULT_QUESTIONS,
    docs_dir: Path = DEFAULT_DOCS,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> int:
    judgments, errors = read_judgments(csv_path)
    original = questions_path.read_bytes().decode("utf-8")
    header = header_of(original)
    data = evalset.load(questions_path)
    if dump_questions(data, header) != original:
        errors.append(f"{questions_path.name}: layout is not reproducible by the serializer")
    truth = evalset.build_truth(docs_dir)
    new = copy.deepcopy(data)
    by_id = {q["id"]: q for q in new["q"]}
    split_of = {q["id"]: q["split"] for q in new["q"]}
    applied: Counter[str] = Counter()
    per_split: Counter[str] = Counter()
    for n, qid, cid, kind in judgments:
        q = by_id.get(qid)
        if q is None:
            errors.append(f"line {n}: unknown qid {qid!r}")
            continue
        if cid not in truth.chunks:
            errors.append(f"line {n}: chunk_id {cid!r} does not exist")
            continue
        if truth.status_of(cid) == "superseded":
            errors.append(f"line {n}: chunk_id {cid!r} is superseded")
            continue
        where = next((k for k in JUDGMENTS if cid in q[k]), None)
        if where:
            errors.append(f"line {n}: {cid!r} is already in {where} of {qid}")
            continue
        q[kind].append(cid)
        applied[kind] += 1
        per_split[split_of[qid]] += 1
    if errors:
        for e in errors:
            print(f"ERROR {e}")
        print("apply_pool: refused, file unchanged")
        return 1
    if not judgments:
        print("no judgments to apply; file unchanged")
        return 0
    text = dump_questions(new, header)
    with tempfile.TemporaryDirectory() as tmp:
        cand = Path(tmp) / "questions.toml"
        cand.write_text(text, encoding="utf-8", newline="")
        result = check_evalset.run_check(cand, docs_dir, manifest_path, evalset.LEVEL_DEFAULT)
    if result.n_fail:
        for f in result.findings:
            if f.level == evalset.FAIL:
                print(f"ERROR check {f.qid}: {f.msg}")
        print("apply_pool: refused (evalset check fails on the result), file unchanged")
        return 1
    fd, tmp_name = tempfile.mkstemp(dir=questions_path.parent, prefix=".questions-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp_name, questions_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    print("by judgment: " + ", ".join(f"{k} {applied[k]}" for k in JUDGMENTS))
    print("by split: " + ", ".join(f"{s} {per_split[s]}" for s in evalset.SPLITS))
    print(f"updated {questions_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", help="judged pool CSV (judgment column filled)")
    ap.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    ap.add_argument("--docs-dir", default=str(DEFAULT_DOCS))
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    args = ap.parse_args(argv)
    return run_apply(
        Path(args.file), Path(args.questions), Path(args.docs_dir), Path(args.manifest)
    )


if __name__ == "__main__":
    sys.exit(main())
