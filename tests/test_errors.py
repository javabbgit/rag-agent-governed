import pytest

from fixtures import fixture_names, load_fixture
from rag_agent.errors import (
    BudgetExceededError,
    GatewayAuthError,
    ModelNotAllowedError,
    PolicyBlockedError,
    RateLimitedError,
    RequestRejectedError,
    UpstreamServerError,
    UpstreamTimeoutError,
    classify_http_error,
)

EXPECTED = {
    "bad_key": GatewayAuthError,
    "not_allowed": ModelNotAllowedError,
    "pii": PolicyBlockedError,
    "non_english": PolicyBlockedError,
}


def _classify(name):
    status, _, parsed = load_fixture(name)
    err = parsed["error"]
    return classify_http_error(status, err["message"], err.get("type"))


def test_every_fixture_has_an_expectation():
    assert set(fixture_names()) == set(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixture_maps_to_expected_class(name):
    exc = _classify(name)
    assert type(exc) is EXPECTED[name]
    assert exc.status_code == load_fixture(name)[0]


def test_policy_fields_parsed():
    exc = _classify("pii")
    assert (exc.rule, exc.level, exc.reason) == ("R10", "high", "detected")
    assert _classify("non_english").reason == "language_unsupported"


def test_auth_message_has_no_key_material():
    exc = _classify("bad_key")
    assert "sk-" not in exc.message
    assert "ba25d198" not in exc.message


def test_configured_key_is_redacted():
    exc = classify_http_error(401, "bad key hunter2-secret", None, secrets=("hunter2-secret",))
    assert "hunter2-secret" not in exc.message


@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        (400, "ExceededBudget: budget has been exceeded", BudgetExceededError),
        (402, "payment required", BudgetExceededError),
        (429, "slow down", RateLimitedError),
        (408, "request timeout", UpstreamTimeoutError),
        (504, "gateway timeout", UpstreamTimeoutError),
        (500, "boom", UpstreamServerError),
        (503, "unavailable", UpstreamServerError),
        (404, "no such route", RequestRejectedError),
        (422, "bad params", RequestRejectedError),
        (403, "something unrecognised", RequestRejectedError),
    ],
)
def test_synthetic_cases(status, message, expected):
    assert type(classify_http_error(status, message)) is expected
