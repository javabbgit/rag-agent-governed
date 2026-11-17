"""Agent evaluation (D8). For now one subcommand:

check [--tasks PATH]   load eval/agent/tasks.toml, resolve every loan selector against the mock
                       data and verify all gold values against the code. No network, no
                       database. Exit 1 on the first error, printed as "<where>: <what>".
"""

from __future__ import annotations

import argparse
import sys

from rag_agent.agent_eval.tasks import TaskFileError, TaskSet, load_tasks


def _task_keys(task) -> str:
    keys = [
        name
        for name, present in (
            ("status", task.status),
            ("ok", task.ok),
            ("not_ok", task.not_ok),
            ("order", task.order),
            ("has", task.has),
            ("has_any", task.has_any),
            ("lacks", task.lacks),
            ("cite", task.cite),
            ("drafts", task.drafts is not None),
            ("draft_to", task.draft_to),
            ("request", task.request),
            ("asks", task.asks),
            ("policy", task.policy),
            ("setup", task.setup),
        )
        if present
    ]
    return " ".join(keys)


def render(ts: TaskSet) -> list[str]:
    lines = []
    for name, sel in ts.selectors.items():
        filters = [
            f"{k}={v}"
            for k, v in (
                ("band", sel.band),
                ("in_progress", sel.in_progress),
                ("stage", sel.stage),
                ("min_conditions", sel.min_conditions),
                ("frequency", sel.frequency),
            )
            if v is not None
        ]
        lines.append(
            f"{name:<12} {sel.who:<6} -> {ts.loans[name]}  {' '.join(filters) or '-'}"
            f"  gold ok ({len(sel.gold)})"
        )
    for task in ts.tasks:
        lines.append(
            f"{task.id:<4} {task.split:<4} {task.category:<14} {task.user:<6} "
            f"checks: {_task_keys(task)}"
        )
    dev = sum(1 for t in ts.tasks if t.split == "dev")
    test = sum(1 for t in ts.tasks if t.split == "test")
    lines.append(
        f"tasks: {len(ts.tasks)} (dev {dev}, test {test}); selectors: {len(ts.selectors)};"
        " gold mismatches: 0"
    )
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="validate the task file and its gold values")
    check.add_argument("--tasks", help="task file (default eval/agent/tasks.toml)")
    args = ap.parse_args(argv)
    try:
        ts = load_tasks(args.tasks)
    except TaskFileError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("\n".join(render(ts)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
