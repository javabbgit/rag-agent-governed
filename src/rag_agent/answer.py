"""Cited answers: prepare (embed, retrieve, rerank, gate, context) then generate (one chat call).

Fail closed. The only endpoint used is the one LLMClient was built with; no code path retries,
switches to another endpoint or another model alias. Gateway errors are the D1 classes from
errors.py, mapped to answer categories by one table (CATEGORY_BY_ERROR).
"""

from __future__ import annotations

import hashlib
import json
import time
import tomllib
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg

from rag_agent import citations as cit
from rag_agent import messages as msg
from rag_agent import retrieve as retrieve_mod
from rag_agent.config import ConfigError, Settings, load_settings
from rag_agent.errors import (
    BudgetExceededError,
    GatewayAuthError,
    GatewayUnavailableError,
    LLMError,
    ModelNotAllowedError,
    PolicyBlockedError,
    RateLimitedError,
    RequestRejectedError,
    UpstreamServerError,
    UpstreamTimeoutError,
)
from rag_agent.ingest import QUERY_PREFIX
from rag_agent.llm_client import LLMClient
from rag_agent.rerank import RerankError, check_pin, reranker_info
from rag_agent.store import StoreError

REPO_ROOT = Path(__file__).resolve().parents[2]
THRESHOLD_PATH = REPO_ROOT / "eval" / "fallback" / "threshold.toml"
THRESHOLD_SIGNAL = "rerank_top1"
TOP_N = 10  # rerank depth the gate and the context are built from
GATE_SOURCES = 3

# One table for every gateway error: D1 class -> (status, answer category).
CATEGORY_BY_ERROR: list[tuple[type[LLMError], str]] = [
    (PolicyBlockedError, "blocked_policy"),
    (BudgetExceededError, "budget_exceeded"),
    (RateLimitedError, "rate_limited"),
    (UpstreamTimeoutError, "timeout"),
    (GatewayUnavailableError, "gateway_unavailable"),
    (UpstreamServerError, "gateway_unavailable"),
    (GatewayAuthError, "auth_config"),
    (ModelNotAllowedError, "auth_config"),
    (RequestRejectedError, "bad_request"),
]


def categorize(exc: LLMError) -> str:
    for cls, category in CATEGORY_BY_ERROR:
        if isinstance(exc, cls):
            return category
    return "gateway_unavailable"  # any other LLMError: fail closed, no detail shown


@dataclass
class Source:
    marker: str | None
    chunk_id: str
    doc_id: str
    title: str
    section: str
    score: float | None
    superseded: bool
    low_relevance: bool = False


@dataclass
class AnswerResult:
    status: str  # answered | fallback | degraded | blocked | error | rejected_input
    subtype: str | None = None  # no_basis | sources_only
    reason: str | None = None
    stage: str | None = None  # input | embed | retrieve | rerank | gate | generate | validate
    message: str = ""
    answer_text: str = ""
    citations: list[Source] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    handoff_suggested: bool = False
    model_name: str | None = None
    call_ids: list[str] = field(default_factory=list)
    cost_usd: float | None = None
    possibly_billed: bool = False
    latency_ms: dict[str, float | None] = field(default_factory=dict)
    prompt_sha256: str | None = None
    threshold: float | None = None
    ungrounded_number_sentences: int = 0
    n_chat_calls: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass
class Prepared:
    question: str
    question_sha256: str
    terminal: AnswerResult | None = None
    top10: list[tuple[str, float, int | None, bool]] = field(default_factory=list)
    context: list[Source] = field(default_factory=list)
    s1: float | None = None
    c1: float | None = None
    latencies: dict[str, float | None] = field(default_factory=dict)
    call_ids: list[str] = field(default_factory=list)
    embedded: bool = False
    # section text of the context chunks, by marker number (1-based), for the user message
    context_text: dict[int, str] = field(default_factory=dict)


# --- startup ---------------------------------------------------------------------------------


def load_threshold(path: Path | None = None) -> float:
    path = path or THRESHOLD_PATH
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ConfigError(
            f"fallback threshold file is missing or unreadable: {path.name}"
        ) from None
    value = data.get("threshold")
    if data.get("signal") != THRESHOLD_SIGNAL:
        raise ConfigError(f"fallback threshold signal must be {THRESHOLD_SIGNAL!r}")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError("fallback threshold file has no numeric threshold")
    return float(value)


def load_prompt(path: str | Path) -> tuple[str, str]:
    """(prompt text, sha256 of the file bytes)."""
    p = Path(path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    try:
        raw = p.read_bytes()
    except OSError:
        raise ConfigError(f"answer prompt file is missing or unreadable: {p.name}") from None
    return raw.decode("utf-8").strip(), hashlib.sha256(raw).hexdigest()


_PIN_CACHE: dict[tuple[str, str], str | None] = {}


def check_reranker(settings: Settings) -> str | None:
    """None when the reranker is up and pinned; else the degraded reason.

    The pin result is cached per process. An unreachable reranker is not cached.
    """
    key = (settings.reranker_url, settings.reranker_model_rev)
    if key in _PIN_CACHE:
        return _PIN_CACHE[key]
    try:
        info = reranker_info(settings.reranker_url, settings.rerank_timeout_s)
    except RerankError:
        return "reranker_unavailable"
    try:
        check_pin(info, settings.reranker_model_rev)
        result = None
    except RerankError:
        result = "reranker_pin_mismatch"
    _PIN_CACHE[key] = result
    return result


@dataclass
class Runtime:
    settings: Settings
    client: Any
    threshold: float
    prompt_text: str
    prompt_sha256: str
    retrieve_fn: Callable[..., Any] = retrieve_mod.retrieve
    sections_fn: Callable[..., Any] = retrieve_mod.section_info
    reranker_check: Callable[[Settings], str | None] = check_reranker


def build_runtime(
    settings: Settings | None = None,
    *,
    client: Any = None,
    client_factory: Callable[[Settings], Any] = LLMClient,
    threshold_path: Path | None = None,
    **overrides: Any,
) -> Runtime:
    """Load settings, threshold and prompt; raise ConfigError when any is unusable."""
    settings = settings or load_settings()
    threshold = load_threshold(threshold_path)
    text, sha = load_prompt(settings.answer_prompt)
    if client is None:
        client = client_factory(settings)
    return Runtime(settings, client, threshold, text, sha, **overrides)


# --- helpers ---------------------------------------------------------------------------------


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def _handoff(status: str) -> bool:
    return status not in ("answered", "rejected_input")


def _result(status: str, rt: Runtime | None = None, **kw: Any) -> AnswerResult:
    res = AnswerResult(status=status, handoff_suggested=_handoff(status), **kw)
    if rt is not None:
        res.prompt_sha256 = rt.prompt_sha256
        res.threshold = rt.threshold
    return res


def _source(info: Any, score: float | None, marker: str | None = None, low: bool = False) -> Source:
    return Source(
        marker=marker,
        chunk_id=info.chunk_id,
        doc_id=info.doc_id,
        title=info.title,
        section=info.section,
        score=score,
        superseded=info.superseded,
        low_relevance=low,
    )


def _error_result(exc: LLMError, rt: Runtime, stage: str) -> AnswerResult:
    category = categorize(exc)
    if category == "blocked_policy":
        res = _result("blocked", rt, reason=category, stage=stage, message=msg.B4)
    else:
        res = _result(
            "error",
            rt,
            reason=category,
            stage=stage,
            message=msg.ERROR_MESSAGES[category],
            possibly_billed=category == "timeout" and stage == "generate",
        )
    if exc.call_id:
        res.call_ids = [exc.call_id]
    return res


# --- prepare ---------------------------------------------------------------------------------


def prepare(question: str, rt: Runtime, *, allow_degraded: bool = True) -> Prepared:
    """Deterministic part: input check, embedding, rerank retrieval, gate, context.

    With allow_degraded=False a reranker failure propagates as RerankError (used by the eval).
    """
    t_start = time.perf_counter()
    settings = rt.settings
    question = question.strip()
    prep = Prepared(question=question, question_sha256=_sha(question))

    def finish(res: AnswerResult) -> Prepared:
        prep.terminal = res
        prep.latencies["total"] = _ms(t_start)
        res.latency_ms = dict(prep.latencies)
        return prep

    if not question:
        return finish(
            _result("rejected_input", rt, reason="empty", stage="input", message=msg.B11a)
        )
    if len(question) > settings.question_max_chars:
        return finish(
            _result("rejected_input", rt, reason="too_long", stage="input", message=msg.B11b)
        )

    # embed (the only gateway call in this function)
    t0 = time.perf_counter()
    prep.embedded = True
    try:
        res = rt.client.embed([QUERY_PREFIX + question], timeout=settings.effective_embed_timeout_s)
        if len(res.vectors) != 1:
            raise LLMError("unexpected embedding count")
        vector = res.vectors[0]
    except LLMError as exc:
        prep.latencies["embed"] = _ms(t0)
        return finish(_error_result(exc, rt, "embed"))
    prep.latencies["embed"] = _ms(t0)

    common: dict[str, Any] = {
        "query_vector": vector,
        "client": rt.client,
        "dsn": settings.database_url,
    }

    def retrieval_unavailable() -> Prepared:
        return finish(
            _result(
                "error",
                rt,
                reason="retrieval_unavailable",
                stage="retrieve",
                message=msg.B8,
            )
        )

    def sections(chunk_ids: list[str]) -> dict[str, Any]:
        found = rt.sections_fn(chunk_ids, dsn=settings.database_url)
        if any(c not in found for c in chunk_ids):
            raise StoreError("chunk missing from the database")
        return found

    def degraded(reason: str) -> Prepared:
        t = time.perf_counter()
        try:
            hits = rt.retrieve_fn(question, mode="hybrid", k=TOP_N, **common)
            hits = [h for h in hits if h.status == "current"][: settings.answer_context_k]
            info = sections([h.chunk_id for h in hits])
        except (psycopg.Error, StoreError):
            return retrieval_unavailable()
        prep.latencies["retrieve"] = _ms(t)
        out = _result("degraded", rt, reason=reason, stage="rerank", message=msg.B3)
        out.sources = [_source(info[h.chunk_id], None) for h in hits]
        return finish(out)

    if allow_degraded:
        problem = rt.reranker_check(settings)
        if problem:
            return degraded(problem)

    t0 = time.perf_counter()
    try:
        hits = rt.retrieve_fn(
            question,
            mode="rerank",
            k=TOP_N,
            reranker_url=settings.reranker_url,
            rerank_timeout=settings.rerank_timeout_s,
            **common,
        )
    except RerankError:
        if not allow_degraded:
            raise
        return degraded("reranker_unavailable")
    except (psycopg.Error, StoreError):
        return retrieval_unavailable()
    prep.latencies["retrieve"] = _ms(t0)  # includes the rerank call
    prep.latencies["rerank"] = None

    prep.top10 = [(h.chunk_id, h.score, h.rrf_rank, h.status != "current") for h in hits]
    t = rt.threshold
    current = [h.score for h in hits if h.status == "current"]
    prep.s1 = hits[0].score if hits else None
    prep.c1 = max(current) if current else None

    try:
        info = sections([h.chunk_id for h in hits])
    except (psycopg.Error, StoreError):
        return retrieval_unavailable()

    if prep.s1 is None or prep.s1 < t or prep.c1 is None or prep.c1 < t:
        reason = "low_score" if prep.s1 is None or prep.s1 < t else "superseded_only"
        out = _result(
            "fallback", rt, subtype="no_basis", reason=reason, stage="gate", message=msg.B1
        )
        closest = [h for h in hits if h.status == "current"][:GATE_SOURCES]
        out.sources = [_source(info[h.chunk_id], h.score, low=True) for h in closest]
        return finish(out)

    kept = [h for h in hits if h.status == "current"][: settings.answer_context_k]
    for i, h in enumerate(kept, start=1):
        prep.context.append(_source(info[h.chunk_id], h.score, marker=f"S{i}"))
        prep.context_text[i] = info[h.chunk_id].text
    prep.latencies["total"] = _ms(t_start)
    return prep


# --- generate --------------------------------------------------------------------------------


def _attr(value: str) -> str:
    return value.replace('"', "'").replace("\n", " ")


def build_user_message(prep: Prepared) -> str:
    blocks = []
    for src in prep.context:
        n = int(src.marker[1:])  # type: ignore[index]
        body = prep.context_text[n].replace("</source>", "</ source>")
        blocks.append(
            f'<source id="{src.marker}" doc="{_attr(src.doc_id)}" title="{_attr(src.title)}"'
            f' section="{_attr(src.section)}">\n{body}\n</source>'
        )
    return "Sources:\n" + "\n".join(blocks) + f"\n\nQuestion: {prep.question}"


def generate(prep: Prepared, rt: Runtime) -> AnswerResult:
    """One chat call, then citation validation. No retry here."""
    if prep.terminal is not None:
        return prep.terminal
    k = len(prep.context)
    messages = [
        {"role": "system", "content": rt.prompt_text},
        {"role": "user", "content": build_user_message(prep)},
    ]
    t0 = time.perf_counter()
    try:
        chat = rt.client.chat(messages, max_tokens=rt.settings.answer_max_tokens)
    except LLMError as exc:
        res = _error_result(exc, rt, "generate")
        res.n_chat_calls = 1
        if res.status == "error":
            res.sources = list(prep.context)
            res.message += " " + msg.SFX
        return _finish_generate(res, prep, t0)

    usage = chat.usage or {}
    base = {
        "model_name": chat.resolved_model,
        "call_ids": [chat.call_id] if chat.call_id else [],
        "cost_usd": chat.cost_usd,
        "n_chat_calls": 1,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
    }
    text = chat.content.strip()
    check = cit.validate(text, k)
    if check.status == "answered":
        res = _result("answered", rt, answer_text=text, **base)
        res.citations = [prep.context[n - 1] for n in check.cited]
        res.sources = [s for s in prep.context if s not in res.citations]
        cited_texts = {n: prep.context_text[n] for n in check.cited}
        res.ungrounded_number_sentences = len(
            cit.ungrounded_numbers(check.units, cited_texts, prep.question)
        )
    elif check.subtype == "no_basis":
        res = _result(
            "fallback",
            rt,
            subtype="no_basis",
            reason=check.reason,
            stage="validate",
            message=msg.B1,
            answer_text=text,
            **base,
        )
        res.sources = [replace(s, low_relevance=True) for s in prep.context[:GATE_SOURCES]]
    else:
        res = _result(
            "fallback",
            rt,
            subtype="sources_only",
            reason=check.reason,
            stage="validate",
            message=msg.B2,
            answer_text=text,
            **base,
        )
        res.sources = list(prep.context)
    return _finish_generate(res, prep, t0)


def _finish_generate(res: AnswerResult, prep: Prepared, t0: float) -> AnswerResult:
    gen = _ms(t0)
    res.latency_ms = {
        **prep.latencies,
        "generate": gen,
        "total": (prep.latencies.get("total") or 0.0) + gen,
    }
    return res


# --- answer + log ----------------------------------------------------------------------------


def log_line(res: AnswerResult, prep: Prepared, rt: Runtime) -> dict[str, Any]:
    """The audit record. Never the question or the answer text."""
    return {
        "ts_utc": datetime.now(UTC).isoformat(),
        "mode": rt.settings.llm_mode,
        "base_url_host": urlparse(rt.settings.openai_base_url).hostname,
        "question_sha256": prep.question_sha256,
        "answer_sha256": _sha(res.answer_text) if res.answer_text else None,
        "status": res.status,
        "subtype": res.subtype,
        "reason": res.reason,
        "stage": res.stage,
        "top10": [[c, s, sup] for c, s, _, sup in prep.top10],
        "context": [[s.marker, s.chunk_id] for s in prep.context],
        "cited": [c.marker for c in res.citations],
        "model_name": res.model_name,
        "call_ids": res.call_ids,
        "cost_usd": res.cost_usd,
        "possibly_billed": res.possibly_billed,
        "latency_ms": res.latency_ms,
        "prompt_sha256": res.prompt_sha256,
        "threshold": res.threshold,
    }


def write_log(line: dict[str, Any], log_dir: str | Path) -> Path:
    d = Path(log_dir)
    if not d.is_absolute():
        d = REPO_ROOT / d
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"answers_{datetime.now(UTC).strftime('%Y-%m-%d')}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=True) + "\n")
    return path


def answer(question: str, rt: Runtime | None = None, *, log: bool = True) -> AnswerResult:
    """prepare, then generate unless prepare already produced a result."""
    rt = rt or build_runtime()
    prep = prepare(question, rt)
    res = generate(prep, rt)
    if log:
        write_log(log_line(res, prep, rt), rt.settings.answer_log_dir)
    return res


def result_dict(res: AnswerResult) -> dict[str, Any]:
    return asdict(res)
