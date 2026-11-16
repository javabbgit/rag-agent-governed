"""Write src/rag_agent/agent/tools.lock from tool_specs.json (make tools-lock).

Human-run: the lock is the pin the agent checks at startup.
"""

from __future__ import annotations

import sys

from rag_agent.agent.errors import AgentError
from rag_agent.agent.specs import LOCK_PATH, load_specs, render_lock


def main() -> int:
    try:
        specs = load_specs()
    except AgentError as exc:
        print(f"tools-lock: {exc}", file=sys.stderr)
        return 1
    LOCK_PATH.write_text(render_lock(specs), encoding="utf-8")
    print(f"wrote {LOCK_PATH.relative_to(LOCK_PATH.parents[3])} ({len(specs)} tools)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
