"""Show an eval question with its chunks, or one chunk. Corpus files only: no network, no DB."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rag_agent import evalset

REPO_ROOT = Path(__file__).resolve().parent.parent


def show_chunk(truth: evalset.Truth, chunk_id: str) -> int:
    c = truth.chunks.get(chunk_id)
    if c is None:
        print(f"unknown chunk: {chunk_id}")
        return 1
    print(f"id: {c.chunk_id}")
    print(f"status: {truth.status_of(chunk_id)}")
    print(f"heading: {c.heading}")
    print()
    print(c.text)
    return 0


def show_question(data: dict, truth: evalset.Truth, qid: str) -> int:
    q = next((x for x in evalset.questions(data) if x.get("id") == qid), None)
    if q is None:
        print(f"unknown question: {qid}")
        return 1
    for key in ("id", "split", "category", "persona", "query", "answer"):
        print(f"{key}: {q.get(key)}")
    for label, ids in (("gold", q.get("gold", [])), ("related", q.get("related", []))):
        for cid in ids:
            print(f"\n--- {label}: {cid}")
            show_chunk(truth, cid)
    print("\nChunks containing the answer:")
    answer = q.get("answer", "")
    if answer:
        for cid, c in truth.chunks.items():
            if not evalset.answer_in(answer, [c.text]):
                continue
            if truth.status_of(cid) == "superseded":
                mark = "superseded"
            elif cid in q.get("gold", []):
                mark = "gold"
            elif cid in q.get("related", []):
                mark = "related"
            elif cid in q.get("nonrel", []):
                mark = "nonrel"
            else:
                mark = "unjudged"
            print(f"  {cid} [{mark}]")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--id")
    g.add_argument("--chunk")
    ap.add_argument("--questions", default=str(REPO_ROOT / "eval" / "retrieval" / "questions.toml"))
    ap.add_argument("--docs-dir", default=str(REPO_ROOT / "corpus" / "docs"))
    args = ap.parse_args(argv)
    truth = evalset.build_truth(args.docs_dir)
    if args.chunk:
        return show_chunk(truth, args.chunk)
    return show_question(evalset.load(args.questions), truth, args.id)


if __name__ == "__main__":
    sys.exit(main())
