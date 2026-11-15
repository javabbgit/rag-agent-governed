"""Ask one question (make ask Q="..."). Sends gateway requests; never part of `make test`.

Exit 0 for any classified result (blocked and error included), 1 for an unhandled exception
(after one generic line), 2 for usage errors.
"""

from __future__ import annotations

import argparse
import json
import sys

from rag_agent import messages
from rag_agent.answer import AnswerResult, Source, answer, build_runtime, result_dict
from rag_agent.config import ConfigError, load_settings


def _src(s: Source) -> str:
    score = "" if s.score is None else f"  score {s.score:.4f}"
    flag = "  (superseded)" if s.superseded else ""
    return f"  [{s.marker or '-'}] {s.chunk_id}  {s.title} / {s.section}{score}{flag}"


def render(res: AnswerResult) -> str:
    lines = [
        f"status:  {res.status}",
        f"subtype: {res.subtype}",
        f"reason:  {res.reason}",
        f"stage:   {res.stage}",
    ]
    if res.message:
        lines += ["", res.message]
    if res.answer_text and res.status == "answered":
        lines += ["", res.answer_text]
    if res.citations:
        lines += ["", "Citations:", *[_src(s) for s in res.citations]]
    if res.sources:
        if any(s.low_relevance for s in res.sources):
            header = messages.B1H
        else:
            header = "Other sources:" if res.citations else "Sources:"
        lines += ["", header, *[_src(s) for s in res.sources]]
    latency = ", ".join(
        f"{k} {v:.0f} ms" for k, v in res.latency_ms.items() if isinstance(v, int | float)
    )
    lines += ["", f"model: {res.model_name}", f"latency: {latency}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("question", nargs="?", default="")
    ap.add_argument("--json", action="store_true", help="print the AnswerResult as JSON")
    ap.add_argument("--env-file", help="path to an env file (default: ./.env if present)")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if not args.question.strip():
        print("usage: ask.py [--json] QUESTION", file=sys.stderr)
        return 2
    try:
        res = answer(args.question, build_runtime(load_settings(args.env_file)))
    except ConfigError as exc:
        print(f"[FAIL] config: {exc}")
        return 1
    except Exception as exc:
        print(f"[FAIL] unexpected error ({type(exc).__name__}); see the logs")
        return 1
    print(json.dumps(result_dict(res), indent=2) if args.json else render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
