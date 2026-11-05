"""Loader for recorded gateway error fixtures (body, then HTTP status on the last line)."""

from __future__ import annotations

import json
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "gateway_errors"


def load_fixture(name: str) -> tuple[int, str, dict]:
    """Return (status_code, raw_body, parsed_json) for <name>.txt."""
    text = (FIXTURE_DIR / f"{name}.txt").read_text(encoding="utf-8").rstrip("\n")
    body, _, status = text.rpartition("\n")
    return int(status.strip()), body, json.loads(body)


def fixture_names() -> list[str]:
    return sorted(p.stem for p in FIXTURE_DIR.glob("*.txt"))
