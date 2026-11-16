"""Send every mock loan and contact record once through the gateway (make agent-prescan).

One chat request per record (the record as JSON in a user message, max_tokens 16) through the
existing client and the configured CHAT_MODEL. Gateway mode only. Prints the request count; the
record text is never printed. Exit 1 on the first error (never retried).
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Iterable
from typing import Any

from rag_agent.agent import mockdata
from rag_agent.config import ConfigError, Settings, load_settings
from rag_agent.llm_client import LLMClient

PAUSE_SECONDS = 0.2


def records() -> list[dict[str, Any]]:
    return [*mockdata.load_loans(), *mockdata.load_contacts()]


def prescan(
    client: Any,
    items: Iterable[dict[str, Any]],
    pause: float = PAUSE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Number of requests sent. Raises on the first client error."""
    sent = 0
    for i, record in enumerate(items):
        if i:
            sleep(pause)
        client.chat(
            [{"role": "user", "content": json.dumps(record, sort_keys=True)}], max_tokens=16
        )
        sent += 1
    return sent


def run(settings: Settings, client: Any = None) -> int:
    if settings.llm_mode != "gateway":
        print("agent-prescan: gateway mode only", file=sys.stderr)
        return 1
    client = client or LLMClient(settings)
    try:
        n = prescan(client, records())
    except Exception as exc:  # any failure stops the scan
        print(f"agent-prescan failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"requests sent: {n}")
    return 0


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"agent-prescan: {exc}", file=sys.stderr)
        return 1
    return run(settings)


if __name__ == "__main__":
    sys.exit(main())
