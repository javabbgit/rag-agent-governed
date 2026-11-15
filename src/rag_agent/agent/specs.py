"""Tool specs, their sha256 pins and tools.lock."""

from __future__ import annotations

import hashlib
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

from rag_agent.agent.errors import SpecInvalid, ToolsLockMismatch
from rag_agent.agent.schema import check_supported

AGENT_DIR = Path(__file__).resolve().parent
SPECS_PATH = AGENT_DIR / "tool_specs.json"
LOCK_PATH = AGENT_DIR / "tools.lock"
TOOL_NAMES = (
    "search_policy",
    "get_loan_status",
    "calculate_payment",
    "draft_reply",
    "prepare_exception_request",
)


def load_specs(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Read and structurally validate the specs; raise SpecInvalid on any problem."""
    p = Path(path or SPECS_PATH)
    try:
        specs = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SpecInvalid(f"tool specs are missing or unreadable: {p.name}") from None
    if not isinstance(specs, list):
        raise SpecInvalid("tool specs must be a list")
    names = []
    for spec in specs:
        fn = spec.get("function") if isinstance(spec, dict) else None
        if (
            not isinstance(fn, dict)
            or spec.get("type") != "function"
            or not isinstance(fn.get("name"), str)
            or not isinstance(fn.get("description"), str)
            or not isinstance(fn.get("parameters"), dict)
        ):
            raise SpecInvalid("a tool spec is malformed")
        params = fn["parameters"]
        if params.get("type") != "object" or params.get("additionalProperties") is not False:
            raise SpecInvalid(f"{fn['name']}: parameters must be an object with no extras")
        check_supported(params, fn["name"])
        names.append(fn["name"])
    if sorted(names) != sorted(TOOL_NAMES):
        raise SpecInvalid("tool specs must define exactly the five agent tools")
    return specs


def tool_hash(tool: dict[str, Any]) -> str:
    text = json.dumps(tool, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hashes(specs: list[dict[str, Any]]) -> dict[str, str]:
    return {s["function"]["name"]: tool_hash(s) for s in specs}


def total_hash(per_tool: dict[str, str]) -> str:
    lines = "\n".join(sorted(f"{name}:{digest}" for name, digest in per_tool.items()))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def render_lock(specs: list[dict[str, Any]]) -> str:
    per_tool = hashes(specs)
    lines = [f'total = "{total_hash(per_tool)}"', "", "[tools]"]
    lines += [f'{name} = "{per_tool[name]}"' for name in sorted(per_tool)]
    return "\n".join(lines) + "\n"


def check_lock(specs: list[dict[str, Any]], lock_path: str | Path | None = None) -> None:
    """Raise ToolsLockMismatch naming changed, missing and extra tools."""
    p = Path(lock_path or LOCK_PATH)
    try:
        lock = tomllib.loads(p.read_text(encoding="utf-8"))
        locked = dict(lock["tools"])
        locked_total = lock["total"]
    except (OSError, ValueError, KeyError, TypeError):
        raise ToolsLockMismatch(detail=f"{p.name} is missing or unreadable") from None
    current = hashes(specs)
    changed = [n for n in current if n in locked and current[n] != locked[n]]
    missing = [n for n in locked if n not in current]
    extra = [n for n in current if n not in locked]
    if changed or missing or extra:
        raise ToolsLockMismatch(changed, missing, extra)
    if total_hash(current) != locked_total:
        raise ToolsLockMismatch(detail="total hash differs")


def load_pinned(
    pinning_on: bool,
    specs_path: str | Path | None = None,
    lock_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Specs checked against the lock. With pinning off a mismatch is one stderr warning."""
    specs = load_specs(specs_path)
    try:
        check_lock(specs, lock_path)
    except ToolsLockMismatch as exc:
        if pinning_on:
            raise
        print(f"warning: tool_pinning is off; {exc}", file=sys.stderr)
    return specs
