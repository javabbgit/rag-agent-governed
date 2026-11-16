"""Agent run log: one JSON line per run. Interactive runs never store question or answer text."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rag_agent.answer import REPO_ROOT
from rag_agent.config import Settings


def _digest(text: str) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "len": len(text)}


def log_line(
    res: Any, settings: Settings, user_id: str, role: str | None, question: str, source: str
) -> dict[str, Any]:
    """The audit record of one run (`res` is a loop.AgentResult)."""
    line: dict[str, Any] = {
        "ts_utc": datetime.now(UTC).isoformat(),
        "run_id": res.run_id,
        "source": source,
        "mode": settings.llm_mode,
        "base_url_host": urlparse(settings.openai_base_url).hostname,
        # an unrecognized id is whatever was typed, so it is stored as a digest
        "user_id": user_id if role is not None else _digest(user_id),
        "role": role,
        "question_sha256": _digest(question)["sha256"],
        "status": res.status,
        "reason": res.reason,
        "finish_reason": res.finish_reason,
        "tool_trace": res.tool_trace,
        "model_names": res.model_names,
        "call_ids": res.call_ids,
        "cost_usd": res.cost_usd,
        "n_chat_calls": res.n_chat_calls,
        "n_tool_calls": res.n_tool_calls,
        "n_denials": res.n_denials,
        "n_embed_calls": res.n_embed_calls,
        "latencies": {
            "total_ms": res.latency_ms_total,
            "chat_ms": res.chat_latency_ms,
            "tools_ms": [t["latency_ms"] for t in res.tool_trace],
        },
        "prompt_sha256": res.prompt_sha256,
        "tools_lock_sha256": res.tools_lock_sha256,
        "policy_sha256": res.policy_sha256,
        "defenses": res.defenses,
        "exception_request_ids": res.exception_request_ids,
        "draft_ids": res.draft_ids,
        "cited_markers": res.cited_markers,
        "n_uncited_units": len(res.uncited_units),
    }
    if source != "interactive":  # smoke questions are ours, so their text may be kept
        line["question"] = question
        line["final_text"] = res.final_text
    return line


def write_log(line: dict[str, Any], log_dir: str | Path) -> Path:
    d = Path(log_dir)
    if not d.is_absolute():
        d = REPO_ROOT / d
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"runs_{datetime.now(UTC).strftime('%Y-%m-%d')}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=True) + "\n")
    return path
