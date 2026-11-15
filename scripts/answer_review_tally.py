"""Tally a filled answer review sheet (make answer-review-tally FILE=...).

Counts supported / partial / unsupported sentences and the questions with any unsupported
sentence. Exits 1 on an empty or unknown verdict (and on an unreadable file).
"""

from __future__ import annotations

import argparse
import csv
import sys

VERDICTS = ("supported", "partial", "unsupported")


def tally(rows: list[dict[str, str]]) -> dict[str, object]:
    counts = dict.fromkeys(VERDICTS, 0)
    bad = []
    unsupported_q: set[str] = set()
    for n, row in enumerate(rows, start=2):  # line 1 is the header
        verdict = (row.get("verdict") or "").strip().lower()
        if verdict not in VERDICTS:
            bad.append(n)
            continue
        counts[verdict] += 1
        if verdict == "unsupported":
            unsupported_q.add(row["qid"])
    return {
        "counts": counts,
        "n_sentences": sum(counts.values()),
        "n_questions": len({r["qid"] for r in rows}),
        "questions_with_unsupported": sorted(unsupported_q),
        "bad_lines": bad,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file")
    args = ap.parse_args(argv)
    try:
        with open(args.file, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    except (OSError, UnicodeDecodeError, csv.Error):
        print("[FAIL] cannot read the review sheet")
        return 1
    if not rows or "verdict" not in rows[0] or "qid" not in rows[0]:
        print("[FAIL] the review sheet has no rows or lacks the qid/verdict columns")
        return 1
    res = tally(rows)
    if res["bad_lines"]:
        print(f"[FAIL] empty or unknown verdict on line(s) {res['bad_lines']}")
        return 1
    c = res["counts"]
    print(
        f"sentences {res['n_sentences']}: supported {c['supported']}, partial {c['partial']}, "
        f"unsupported {c['unsupported']}"
    )
    q = res["questions_with_unsupported"]
    print(f"questions with any unsupported sentence: {len(q)} of {res['n_questions']} {q}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
