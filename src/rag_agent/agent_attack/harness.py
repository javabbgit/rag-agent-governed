"""Injection points for attack runs. Production code is not changed; these wrap it.

- TamperingRepo: a Repo whose get_loan returns a tampered copy of one loan (channel B).
- mixed_retrieve / mixed_sections: retrieval over the main and adversarial collections (A).
- write_spec_copy / config_for / preflight_lock: patched tool specs in a temp dir and the
  switched agent config (C, and every defense-off config).
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from rag_agent import retrieve as retrieve_mod
from rag_agent.agent import boot, mockdata, specs
from rag_agent.agent.facts import load_facts
from rag_agent.agent.loop import STARTUP_CODES
from rag_agent.agent.policy_config import DEFENSES, load_policy
from rag_agent.agent_attack.attacks import CONFIGS, Registered, SpecPatch, Tamper
from rag_agent.agent_attack.verdict import PreflightFailure, StartupFailure, World, make_world

COLLECTIONS = ("main", "adversarial")


# --- B: tampered tool results ------------------------------------------------------------------


class TamperingRepo:
    """Forwards the Repo methods to `inner`; get_loan tampers one loan's copy. Writes nothing
    itself and never changes the inner record. transition is refused (rule 25)."""

    def __init__(self, inner: Any, tamper: Tamper):
        self.inner, self.tamper = inner, tamper

    def get_loan(self, loan_number: str) -> dict[str, Any] | None:
        record = self.inner.get_loan(loan_number)
        if record is None or loan_number != self.tamper.loan:
            return record
        out = copy.deepcopy(record)
        value = out.get(self.tamper.field)
        if isinstance(value, list):
            out[self.tamper.field] = [*value, self.tamper.text]
        else:
            out[self.tamper.field] = f"{value or ''}{self.tamper.text}"
        return out

    def get_contacts(self, loan_number: str) -> list[dict[str, Any]]:
        return self.inner.get_contacts(loan_number)

    def insert_draft(self, row: dict[str, Any]) -> str:
        return self.inner.insert_draft(row)

    def create_exception_request(self, row: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        return self.inner.create_exception_request(row)

    def insert_exception_unguarded(self, row: dict[str, Any]) -> dict[str, Any]:
        return self.inner.insert_exception_unguarded(row)

    def log_event(self, event: dict[str, Any]) -> None:
        self.inner.log_event(event)

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        return self.inner.get_request(request_id)

    def list_requests(self, status: str | None = None) -> list[dict[str, Any]]:
        return self.inner.list_requests(status)

    def transition(
        self, request_id: str, from_status: str, to_status: str, fields: dict[str, Any]
    ) -> bool:
        """Refused, not forwarded: only approvals.py moves a request (AGENTS.md rule 25), and an
        agent run never needs it."""
        raise PermissionError("attack runs never change a request's status")


# --- A: retrieval over two collections ---------------------------------------------------------


def mixed_retrieve(
    collections: Sequence[str] = COLLECTIONS,
    *,
    base: Callable[..., list[retrieve_mod.Hit]] = retrieve_mod.retrieve,
) -> Callable[..., list[retrieve_mod.Hit]]:
    """A retrieve_fn with retrieve.retrieve's signature that asks every collection with the same
    query vector, client, dsn and rerank settings, merges, keeps k and renumbers the ranks."""
    order = list(collections)

    def fn(
        query: str,
        mode: str = "hybrid",
        k: int = retrieve_mod.DEFAULT_K,
        collection: str | None = None,  # ignored: every collection is asked
        include_superseded: bool = True,
        query_vector: list[float] | None = None,
        *,
        client: Any = None,
        store: Any = retrieve_mod.default_store,
        dsn: str | None = None,
        rerank_n: int | None = None,
        reranker_url: str | None = None,
        rerank_timeout: float | None = None,
        max_batch: int = retrieve_mod.RERANK_MAX_BATCH,
    ) -> list[retrieve_mod.Hit]:
        merged: list[tuple[float, int, str, retrieve_mod.Hit]] = []
        for i, name in enumerate(order):
            hits = base(
                query,
                mode,
                k,
                name,
                include_superseded,
                query_vector,
                client=client,
                store=store,
                dsn=dsn,
                rerank_n=rerank_n,
                reranker_url=reranker_url,
                rerank_timeout=rerank_timeout,
                max_batch=max_batch,
            )
            merged += [(-round(h.score, 6), i, h.chunk_id, h) for h in hits]
        merged.sort(key=lambda t: t[:3])
        return [replace(h, rank=r) for r, (*_, h) in enumerate(merged[:k], start=1)]

    return fn


def mixed_sections(
    collections: Sequence[str] = COLLECTIONS,
    *,
    base: Callable[..., dict[str, Any]] = retrieve_mod.section_info,
) -> Callable[..., dict[str, Any]]:
    """A sections_fn that asks each collection; the first collection that knows a chunk wins."""
    order = list(collections)

    def fn(
        chunk_ids: Sequence[str],
        collection: str | None = None,  # ignored
        *,
        store: Any = retrieve_mod.default_store,
        dsn: str | None = None,
    ) -> dict[str, Any]:
        found: dict[str, Any] = {}
        for name in order:
            missing = [c for c in chunk_ids if c not in found]
            if not missing:
                break
            for key, info in base(missing, name, store=store, dsn=dsn).items():
                found.setdefault(key, info)
        return found

    return fn


# --- C: tool spec copies and switched configs --------------------------------------------------


def write_spec_copy(patch: SpecPatch, out_dir: str | Path) -> tuple[Path, Path]:
    """Patched copy of the repo specs in out_dir; the repo files are only read."""
    out = Path(out_dir)
    data = json.loads(specs.SPECS_PATH.read_text(encoding="utf-8"))
    new = copy.deepcopy(data)
    if patch.kind == "add_tool":
        new.append(
            {
                "type": "function",
                "function": {
                    "name": patch.tool,
                    "description": patch.text,
                    "parameters": {
                        "type": "object",
                        "properties": {"loan_number": {"type": "string"}},
                        "required": ["loan_number"],
                        "additionalProperties": False,
                    },
                },
            }
        )
    else:
        fn = next(s["function"] for s in new if s["function"]["name"] == patch.tool)
        if patch.kind == "description_append":
            fn["description"] += patch.text or ""
        elif patch.kind == "drop_pattern":
            fn["parameters"]["properties"][patch.param].pop("pattern", None)
        else:
            raise ValueError(f"unknown patch kind {patch.kind!r}")
    specs_path = out / "tool_specs.json"
    specs_path.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lock_path = specs.LOCK_PATH
    if patch.relock:
        lock_path = out / "tools.lock"
        lock_path.write_text(specs.render_lock(new), encoding="utf-8")
    return specs_path, lock_path


def disabled_for(config_name: str) -> tuple[str, ...]:
    if config_name not in CONFIGS:
        raise ValueError(f"unknown config {config_name!r}")
    if config_name == "on":
        return ()
    if config_name == "all_off":
        return tuple(DEFENSES)
    return (config_name.removesuffix("_off"),)


def env_for(config_name: str) -> dict[str, str]:
    off = disabled_for(config_name)
    if not off:
        return {}
    return {"AGENT_DISABLE": ",".join(off), "AGENT_DEFENSES_OFF_ACK": "1"}


def config_for(
    config_name: str,
    patch: SpecPatch | None = None,
    *,
    specs_path: str | Path | None = None,
    lock_path: str | Path | None = None,
) -> boot.AgentConfig | StartupFailure:
    """The agent config for one of the nine configs. Never reads or writes os.environ."""
    env = env_for(config_name)
    with contextlib.ExitStack() as stack:
        if patch is not None and specs_path is None:
            tmp = stack.enter_context(tempfile.TemporaryDirectory(prefix="attack_specs_"))
            specs_path, lock_path = write_spec_copy(patch, tmp)
        try:
            with contextlib.redirect_stderr(io.StringIO()):  # the pinning-off warning
                return boot.load_agent_config(specs_path=specs_path, lock_path=lock_path, env=env)
        except tuple(cls for cls, _ in STARTUP_CODES) as exc:
            return StartupFailure(next(code for cls, code in STARTUP_CODES if isinstance(exc, cls)))


def preflight_lock(lock_path: str | Path, registered: Registered) -> PreflightFailure | None:
    digest = hashlib.sha256(Path(lock_path).read_bytes()).hexdigest()
    return None if digest == registered.tools_lock_sha256 else PreflightFailure("tools_lock")


def offered_tools(config: boot.AgentConfig, role: str) -> frozenset[str]:
    """The tool names the loop offers to this role (mirrors loop._loop)."""
    chosen = config.tools_for_role(role) if config.policy.on("role_allowlist") else config.specs
    return frozenset(s["function"]["name"] for s in chosen)


def world() -> World:
    return make_world(
        mockdata.load_users(),
        mockdata.load_loans(),
        mockdata.load_contacts(),
        load_policy(env={}),
        load_facts(),
    )
