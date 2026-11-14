"""Turn a pool CSV (make eval-pool) into a judging sheet. No network, no database.

Writes next to the input: <name>_judge.csv (pool rows + answer, contains_answer; judgment emptied;
contains_answer yes first, then qid, best_rank) and <name>_judge.md (one section per row with the
chunk's full text). Exit 1 on an unknown qid or chunk_id.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from rag_agent import evalset

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUESTIONS = REPO_ROOT / "eval" / "retrieval" / "questions.toml"
DEFAULT_DOCS = REPO_ROOT / "corpus" / "docs"
EXTRA = ("answer", "contains_answer")


def read_pool(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = [f.strip() for f in (reader.fieldnames or [])]
        reader.fieldnames = fields
        rows = [{k: (v or "") for k, v in row.items() if k is not None} for row in reader]
    return fields, rows


def build_sheet(
    fields: list[str],
    rows: list[dict[str, str]],
    questions: dict[str, dict],
    truth: evalset.Truth,
) -> tuple[list[str], list[dict[str, str]], list[str]]:
    errors: list[str] = []
    for col in ("qid", "chunk_id", "best_rank"):
        if col not in fields:
            errors.append(f"missing column {col!r}")
    if errors:
        return fields, [], errors
    out: list[dict[str, str]] = []
    for n, row in enumerate(rows, start=2):
        qid, cid = row["qid"].strip(), row["chunk_id"].strip()
        q = questions.get(qid)
        if q is None:
            errors.append(f"line {n}: unknown qid {qid!r}")
            continue
        chunk = truth.chunks.get(cid)
        if chunk is None:
            errors.append(f"line {n}: unknown chunk_id {cid!r}")
            continue
        try:
            int(row["best_rank"])
        except ValueError:
            errors.append(f"line {n}: best_rank {row['best_rank']!r} is not an integer")
            continue
        new = dict(row)
        new["answer"] = q["answer"]
        new["contains_answer"] = "yes" if evalset.answer_in(q["answer"], [chunk.text]) else "no"
        if "judgment" in new:
            new["judgment"] = ""
        out.append(new)
    out.sort(
        key=lambda r: (r["contains_answer"] != "yes", r["qid"], int(r["best_rank"]), r["chunk_id"])
    )
    cols = list(fields) + [c for c in EXTRA if c not in fields]
    return cols, out, errors


def markdown(rows: list[dict[str, str]], questions: dict[str, dict], truth: evalset.Truth) -> str:
    parts = ["# Pool judging sheet\n"]
    for r in rows:
        q = questions[r["qid"]]
        chunk = truth.chunks[r["chunk_id"]]
        gold = ", ".join(q["gold"]) or "(none)"
        parts.append(
            f"## {r['qid']} - {r['chunk_id']}\n\n"
            f"- qid: {r['qid']}\n"
            f"- query: {q['query']}\n"
            f"- answer: {q['answer']}\n"
            f"- current gold: {gold}\n"
            f"- candidate chunk: {r['chunk_id']} ({chunk.heading})\n"
            f"- ranks: {r.get('ranks', '')} (best {r['best_rank']})\n"
            f"- contains_answer: {r['contains_answer']}\n\n"
            f"{chunk.text.strip()}\n"
        )
    return "\n".join(parts)


def run_sheet(pool_path: Path, questions_path: Path, docs_dir: Path) -> int:
    fields, rows = read_pool(pool_path)
    questions = {q["id"]: q for q in evalset.questions(evalset.load(questions_path))}
    truth = evalset.build_truth(docs_dir)
    cols, out, errors = build_sheet(fields, rows, questions, truth)
    if errors:
        for e in errors:
            print(f"ERROR {e}")
        print("pool_sheet: refused, nothing written")
        return 1
    stem = pool_path.with_suffix("")
    csv_path = stem.with_name(stem.name + "_judge.csv")
    md_path = stem.with_name(stem.name + "_judge.md")
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    with open(md_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(markdown(out, questions, truth))
    n_yes = sum(r["contains_answer"] == "yes" for r in out)
    print(f"rows {len(out)} (contains_answer yes {n_yes}, no {len(out) - n_yes})")
    print(f"wrote {csv_path}")
    print(f"wrote {md_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", help="pool CSV written by make eval-pool")
    ap.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    ap.add_argument("--docs-dir", default=str(DEFAULT_DOCS))
    args = ap.parse_args(argv)
    return run_sheet(Path(args.file), Path(args.questions), Path(args.docs_dir))


if __name__ == "__main__":
    sys.exit(main())
