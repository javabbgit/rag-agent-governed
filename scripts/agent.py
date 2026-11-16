"""Run the agent once (make agent Q="..." AS=<user_id> [JSON=1]). Sends gateway requests; never
part of `make test`.

Exit 0 for any classified result (blocked and error included), 1 for a startup error or an
unhandled exception (after one line), 2 for usage errors.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from rag_agent.agent import loop
from rag_agent.agent.errors import AgentStartupError
from rag_agent.agent.loop import AgentResult


def banner(defenses: dict[str, bool]) -> str | None:
    off = [name for name, on in defenses.items() if not on]
    return f"DEFENSES OFF: {', '.join(off)}" if off else None


def render(res: AgentResult) -> str:
    lines = [f"status: {res.status}", f"reason: {res.reason}", "", res.message]
    if res.tool_trace:
        lines += ["", "Tool trace:", "  turn  stage  tool                       status  reason"]
        for t in res.tool_trace:
            lines.append(
                f"  {t['turn']:<4}  {t['rule_stage']:<5}  {t['tool']:<25}  {t['status']}"
                f"  {t['reason'] or '-'}  ({t['latency_ms']:.0f} ms)"
            )
    if res.sources:
        lines += ["", "Sources:", *[f"  [{marker}] {chunk_id}" for marker, chunk_id in res.sources]]
    cost = "n/a" if res.cost_usd is None else f"{res.cost_usd:.6f} USD"
    lines += [
        "",
        f"exception requests: {', '.join(res.exception_request_ids) or '-'}",
        f"drafts: {', '.join(res.draft_ids) or '-'}",
        f"models: {', '.join(res.model_names) or '-'}",
        f"cost: {cost}",
        f"latency: {res.latency_ms_total:.0f} ms  ({res.n_chat_calls} chat calls,"
        f" {res.n_tool_calls} tool calls, run {res.run_id})",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("question", nargs="?", default=None)
    ap.add_argument("--as", dest="user", default=None, help="user id, for example uw-01")
    ap.add_argument("--json", action="store_true", help="print the AgentResult as JSON")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if args.question is None or not args.user:
        print("usage: agent.py [--json] --as USER_ID QUESTION", file=sys.stderr)
        return 2
    try:
        loop.startup()
        res = loop.run_agent(args.question, args.user)
    except AgentStartupError as exc:
        print(f"[FAIL] startup: {exc}")
        return 1
    except Exception as exc:
        print(f"[FAIL] unexpected error ({type(exc).__name__}); see the logs")
        return 1
    line = banner(res.defenses)
    if line:
        print(line)
    print(json.dumps(asdict(res), indent=2) if args.json else render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
