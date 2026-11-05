"""Connectivity smoke test for the configured LLM endpoint(s) and pgvector.

Each check is independently PASS / FAIL / SKIPPED. Writes reports/smoke/<UTC>_<mode>.json
(never keys). Exit code 1 if any check fails.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from rag_agent.config import ConfigError, Settings, load_settings
from rag_agent.errors import (
    LLMError,
    PolicyBlockedError,
    RequestRejectedError,
    UpstreamTimeoutError,
    sanitize,
)
from rag_agent.llm_client import LLMClient

REPO_ROOT = Path(__file__).resolve().parent.parent
PASS, FAIL, SKIPPED = "PASS", "FAIL", "SKIPPED"

# The gateway's own FAKE_SIN (scripts/p4check.py / corpus H01): Luhn-valid, not anyone's number.
# The single allowed exception to the "no 9-digit numbers" rule; used only for the PII probe.
PII_PROBE_TEXT = "Applicant SIN: 193 456 787. Please verify the employment letter."

GROUPS = {
    "models": ["models"],
    "chat": ["chat"],
    "embed": ["embed_plain", "embed_loan_id"],
    "pii": ["pii"],
    "db": ["db"],
}


def result(status: str, started: float | None, detail: str, **extra: Any) -> dict[str, Any]:
    latency = None if started is None else round((time.perf_counter() - started) * 1000, 1)
    return {"status": status, "latency_ms": latency, "detail": detail, **extra}


def vector_sha256(vec: list[float]) -> str:
    return hashlib.sha256(",".join(f"{v:.6f}" for v in vec).encode()).hexdigest()


def guarded(fn: Callable[[], dict[str, Any]], s: Settings, *secrets: str | None) -> dict[str, Any]:
    """Run a check; turn any exception into FAIL.

    Messages are always sanitized with both API keys plus any extra secrets.
    """
    secrets = (s.openai_api_key, s.effective_embed_api_key, *secrets)
    started = time.perf_counter()
    try:
        return fn()
    except UpstreamTimeoutError as exc:
        return result(FAIL, started, f"{type(exc).__name__}: {exc.message}", timeout=True)
    except LLMError as exc:
        return result(FAIL, started, f"{type(exc).__name__}: {exc.message}")
    except Exception as exc:
        msg = sanitize(str(exc), *secrets).strip().splitlines()
        first = msg[0][:300] if msg else ""
        name = type(exc).__name__
        return result(FAIL, started, f"{name}: {first}" if first else name)


def check_models(client: LLMClient, s: Settings) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        ids = client.list_models()
    except RequestRejectedError as exc:
        if s.llm_mode == "direct":
            return result(SKIPPED, started, "model listing not supported by this endpoint")
        if exc.status_code in (404, 405, 501):
            return result(SKIPPED, started, "endpoint does not support model listing")
        raise
    if s.chat_model in ids:
        return result(PASS, started, f"{s.chat_model} listed ({len(ids)} models)")
    return result(FAIL, started, f"{s.chat_model} not in {len(ids)} listed models")


def check_chat(client: LLMClient, s: Settings) -> dict[str, Any]:
    started = time.perf_counter()
    res = client.chat([{"role": "user", "content": "Reply with exactly: PONG"}])
    ok = "PONG" in res.content
    return result(
        PASS if ok else FAIL,
        started,
        "reply contains PONG" if ok else "reply did not contain PONG",
        response_model=res.model,
        resolved_model=res.resolved_model,
        usage=res.usage,
    )


def check_embed_plain(client: LLMClient, s: Settings, state: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    res = client.embed(["Demo Mortgage Co. prepayment policy"])
    vec = res.vectors[0]
    state["vector"] = vec
    ok = len(vec) == s.embed_dim
    return result(
        PASS if ok else FAIL,
        started,
        f"length {len(vec)} (EMBED_DIM {s.embed_dim})",
        model=res.model,
        first5=[round(v, 6) for v in vec[:5]],
        vector_sha256=vector_sha256(vec),
    )


def check_embed_loan_id(client: LLMClient, s: Settings) -> dict[str, Any]:
    started = time.perf_counter()
    res = client.embed(["Status of loan DMC-2026-00123"])
    return result(PASS, started, f"not blocked, length {len(res.vectors[0])}", model=res.model)


def check_pii(client: LLMClient, s: Settings) -> dict[str, Any]:
    """Expect a block (rule R10) or a reroute away from the requested alias. Reply never shown."""
    started = time.perf_counter()
    try:
        res = client.chat([{"role": "user", "content": PII_PROBE_TEXT}])
    except PolicyBlockedError as exc:
        return result(
            PASS,
            started,
            "blocked",
            rule=exc.rule,
            level=exc.level,
            reason=exc.reason,
            status_code=exc.status_code,
        )
    routed = res.governance.get("x-governance-routed-model")
    extra = {
        "x_governance_reason": res.governance.get("x-governance-reason"),
        "x_governance_routed_model": routed,
        "resolved_model": res.resolved_model,
    }
    if routed and routed != s.chat_model:
        return result(PASS, started, f"rerouted to {routed}", **extra)
    return result(
        FAIL, started, "request was answered by the requested model (not blocked)", **extra
    )


def check_db(s: Settings, state: dict[str, Any]) -> dict[str, Any]:
    import psycopg
    from pgvector import Vector
    from pgvector.psycopg import register_vector

    started = time.perf_counter()
    vec = state.get("vector")
    note = ""
    if vec is None:  # --only db: no network call; use a fixed unit-ish vector
        vec = [1.0] + [0.0] * (s.embed_dim - 1)
        note = " (synthetic vector; embed check not run)"
    vec_param = Vector(vec)
    with psycopg.connect(s.database_url, autocommit=True, connect_timeout=10) as conn:
        row = conn.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'").fetchone()
        if row is None:
            return result(FAIL, started, "vector extension not installed")
        register_vector(conn)
        conn.execute(f"CREATE TEMP TABLE smoke_vec (id int, v vector({int(s.embed_dim)}))")
        conn.execute("INSERT INTO smoke_vec VALUES (1, %s)", (vec_param,))
        cur = conn.execute("SELECT v <=> %s FROM smoke_vec WHERE id = 1", (vec_param,))
        dist = cur.fetchone()[0]
    ok = dist is not None and abs(dist) < 1e-6
    return result(PASS if ok else FAIL, started, f"cosine distance to self = {dist}{note}")


def git_info() -> tuple[str | None, bool | None]:
    def run(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    commit = run("rev-parse", "HEAD")
    status = run("status", "--porcelain")
    return commit, (None if status is None else bool(status))


def run_checks(s: Settings, only: str | None, pii: bool) -> dict[str, dict[str, Any]]:
    wanted = set(GROUPS[only]) if only else {n for g in GROUPS.values() for n in g}
    client = LLMClient(s)
    state: dict[str, Any] = {}
    checks: dict[str, dict[str, Any]] = {}

    def run(name: str, fn: Callable[[], dict[str, Any]]) -> None:
        if name in wanted:
            checks[name] = guarded(fn, s)

    run("models", lambda: check_models(client, s))
    run("chat", lambda: check_chat(client, s))
    run("embed_plain", lambda: check_embed_plain(client, s, state))
    run("embed_loan_id", lambda: check_embed_loan_id(client, s))
    if "pii" in wanted:
        if not pii:
            checks["pii"] = result(SKIPPED, None, "--pii not given")
        elif s.llm_mode != "gateway":
            checks["pii"] = result(SKIPPED, None, "PII probe only applies in gateway mode")
        else:
            checks["pii"] = guarded(lambda: check_pii(client, s), s)
    if "db" in wanted:
        password = urlsplit(s.database_url).password
        checks["db"] = guarded(lambda: check_db(s, state), s, s.database_url, password)
    return checks


def build_report(s: Settings, checks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    commit, dirty = git_info()
    statuses = [c["status"] for c in checks.values()]
    return {
        "timestamp_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": commit,
        "git_dirty": dirty,
        "mode": s.llm_mode,
        "chat_base_url": s.openai_base_url,
        "embed_base_url": s.effective_embed_base_url,
        "chat_model": s.chat_model,
        "embed_model": s.embed_model,
        "temperature": None,  # requested value: smoke never sends one
        "checks": checks,
        "counts": {
            "pass": statuses.count(PASS),
            "fail": statuses.count(FAIL),
            "skipped": statuses.count(SKIPPED),
            "timeouts": sum(1 for c in checks.values() if c.get("timeout")),
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env-file", help="path to an env file (default: ./.env if present)")
    ap.add_argument("--pii", action="store_true", help="run the PII probe (gateway mode only)")
    ap.add_argument("--only", choices=sorted(GROUPS), help="run a single group of checks")
    ap.add_argument("-v", "--verbose", action="store_true", help="log request URLs (no headers)")
    args = ap.parse_args(argv)

    if args.verbose:
        logging.basicConfig(format="  %(message)s")
        logging.getLogger("httpx").setLevel(logging.INFO)

    try:
        s = load_settings(args.env_file)
    except ConfigError as exc:
        print(f"[FAIL] config: {exc}")
        return 1

    print("[PASS] config")
    for k, v in s.masked_summary().items():
        print(f"    {k}: {v}")
    print(f"    mode: {s.llm_mode}")

    pii = args.pii or args.only == "pii"
    checks = run_checks(s, args.only, pii)
    checks = {
        "config": {
            "status": PASS,
            "latency_ms": 0.0,
            "detail": "loaded",
            "summary": s.masked_summary(),
        },
        **checks,
    }
    for name, c in checks.items():
        if name != "config":
            lat = "-" if c["latency_ms"] is None else f"{c['latency_ms']} ms"
            print(f"[{c['status']}] {name} ({lat}): {c['detail']}")

    report = build_report(s, checks)
    out_dir = REPO_ROOT / "reports" / "smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{stamp}_{s.llm_mode}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    c = report["counts"]
    print(f"pass={c['pass']} fail={c['fail']} skipped={c['skipped']} timeouts={c['timeouts']}")
    print(f"report: {path.relative_to(REPO_ROOT)}")
    return 1 if c["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
