"""Loaders for data/mock/*.toml and the helpers that derive things from a loan record."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MOCK_DIR = Path(__file__).resolve().parents[3] / "data" / "mock"

IN_PROGRESS_STAGES = ("application_received", "conditionally_approved", "conditions_outstanding")


@dataclass(frozen=True)
class User:
    user_id: str
    role: str
    org: str | None = None


def _read(name: str, key: str, mock_dir: Path | None) -> list[dict[str, Any]]:
    path = (mock_dir or MOCK_DIR) / name
    return list(tomllib.loads(path.read_text(encoding="utf-8"))[key])


def load_users(mock_dir: Path | None = None) -> dict[str, User]:
    rows = _read("users.toml", "users", mock_dir)
    return {r["user_id"]: User(r["user_id"], r["role"], r.get("org")) for r in rows}


def load_loans(mock_dir: Path | None = None) -> list[dict[str, Any]]:
    return sorted(_read("loans.toml", "loans", mock_dir), key=lambda r: r["loan_number"])


def load_contacts(mock_dir: Path | None = None) -> list[dict[str, Any]]:
    return sorted(_read("contacts.toml", "contacts", mock_dir), key=lambda r: r["contact_id"])


def visible_to(loan: dict[str, Any], user: User, scope: str) -> bool:
    """Scope rule shared by the policy layer and agent-pick."""
    if scope == "all":
        return True
    if scope == "own_submitted":
        return loan["broker_id"] == user.user_id
    if scope == "assigned":
        return loan["underwriter_id"] == user.user_id
    return False
