"""Error taxonomy for model calls. Fail closed: nothing here triggers a fallback."""

from __future__ import annotations

import re


class LLMError(Exception):
    """Base class. `message` is safe to log and print (never contains keys)."""

    default_status: int | None = None

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code if status_code is not None else self.default_status


class GatewayAuthError(LLMError):
    default_status = 401


class ModelNotAllowedError(LLMError):
    default_status = 403


class PolicyBlockedError(LLMError):
    default_status = 403

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message, status_code)
        info = parse_policy_message(message)
        self.rule = info.get("rule")
        self.level = info.get("level")
        self.reason = info.get("reason")


class BudgetExceededError(LLMError):
    default_status = 400


class RateLimitedError(LLMError):
    default_status = 429


class UpstreamTimeoutError(LLMError):
    default_status = 408


class GatewayUnavailableError(LLMError):
    """Connection refused / DNS failure. status_code is None."""


class UpstreamServerError(LLMError):
    default_status = 500


class RequestRejectedError(LLMError):
    """Any other 4xx. Not retried."""


# --- classification -------------------------------------------------------------------------

_POLICY_RE = re.compile(r"policy_blocked:\s*rule\s+(?P<rule>\S+)")
_LEVEL_RE = re.compile(r"\(level\s+(?P<level>[^,)]+),\s*reason\s+(?P<reason>[^)]+)\)")
_SECRET_RE = re.compile(r"sk-[A-Za-z0-9._-]*")
_KEYHASH_RE = re.compile(r"Key Hash \(Token\)\s*=\s*\S+", re.IGNORECASE)
_BUDGET_RE = re.compile(r"budget|ExceededBudget|max_budget|spend limit", re.IGNORECASE)
_ALLOWLIST_RE = re.compile(
    r"key_model_access_denied|not available for this API key|not allowed to access model",
    re.IGNORECASE,
)
_TIMEOUT_RE = re.compile(r"timed? ?out|timeout", re.IGNORECASE)


def parse_policy_message(message: str) -> dict[str, str]:
    out: dict[str, str] = {}
    m = _POLICY_RE.search(message)
    if m:
        out["rule"] = m.group("rule").rstrip(".,;")
    m = _LEVEL_RE.search(message)
    if m:
        out["level"] = m.group("level").strip()
        out["reason"] = m.group("reason").strip()
    return out


def sanitize(message: str, *secrets: str | None) -> str:
    """Strip key material from a gateway message."""
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    message = _KEYHASH_RE.sub("Key Hash (Token) = [redacted]", message)
    return _SECRET_RE.sub("[redacted]", message)


def classify_http_error(
    status_code: int,
    message: str,
    error_type: str | None = None,
    *,
    secrets: tuple[str | None, ...] = (),
) -> LLMError:
    """Map an HTTP status plus gateway message/type to the taxonomy.

    Patterns are derived from tests/fixtures/gateway_errors/*; budget, rate-limit and
    timeout patterns are LiteLLM conventions not yet covered by recorded fixtures.
    """
    haystack = f"{error_type or ''} {message}"
    safe = sanitize(message, *secrets)

    if status_code == 401:
        return GatewayAuthError(safe, status_code)
    if status_code == 403 and _ALLOWLIST_RE.search(haystack):
        return ModelNotAllowedError(safe, status_code)
    if "policy_blocked" in haystack:
        return PolicyBlockedError(safe, status_code)
    if _BUDGET_RE.search(haystack) and status_code in (400, 402, 403, 429):
        return BudgetExceededError(safe, status_code)
    if status_code == 402:
        return BudgetExceededError(safe, status_code)
    if status_code == 429:
        return RateLimitedError(safe, status_code)
    if status_code in (408, 504) or (status_code >= 500 and _TIMEOUT_RE.search(haystack)):
        return UpstreamTimeoutError(safe, status_code)
    if status_code >= 500:
        return UpstreamServerError(safe, status_code)
    return RequestRejectedError(safe, status_code)
