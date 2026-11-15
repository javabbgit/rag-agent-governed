"""Replay every recorded gateway response through LLMClient and the answer error mapping."""

import httpx2
import pytest

from answer_fakes import FakeRetriever, hits_from, sections
from fixtures import FIXTURE_DIR, fixture_names, load_fixture
from rag_agent import answer as ans
from rag_agent import messages as msg
from rag_agent.errors import (
    BudgetExceededError,
    GatewayAuthError,
    ModelNotAllowedError,
    PolicyBlockedError,
    RateLimitedError,
    RequestRejectedError,
    UpstreamServerError,
)
from rag_agent.llm_client import LLMClient

OK_DIR = FIXTURE_DIR.parent / "gateway_ok"

EXPECTED = {
    "bad_key": (GatewayAuthError, "auth_config"),
    "empty_key": (GatewayAuthError, "auth_config"),
    "not_allowed": (ModelNotAllowedError, "auth_config"),
    "pii": (PolicyBlockedError, "blocked_policy"),
    "blocked_tool_message": (PolicyBlockedError, "blocked_policy"),
    "non_english": (PolicyBlockedError, "blocked_policy"),
    "budget_exceeded": (BudgetExceededError, "budget_exceeded"),
    "rate_limited": (RateLimitedError, "rate_limited"),
    "upstream_5xx": (UpstreamServerError, "gateway_unavailable"),
}
EMBED_OK = {"data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3, 0.4]}]}


def read_headers(path):
    """(status, {name: value}) from raw `curl -D` output; CRLF or LF."""
    lines = path.read_text(encoding="utf-8").splitlines()
    status = int(lines[0].split()[1])
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
    for drop in ("content-length", "transfer-encoding", "content-encoding"):
        headers.pop(drop, None)
    return status, headers


def error_response(name):
    status, headers = read_headers(FIXTURE_DIR / f"{name}.headers")
    _, body, _ = load_fixture(name)
    return httpx2.Response(status, headers=headers, content=body.encode())


def ok_response():
    status, headers = read_headers(OK_DIR / "chat_ok.headers")
    body = (OK_DIR / "chat_ok.txt").read_text(encoding="utf-8").strip()
    return httpx2.Response(status, headers=headers, content=body.encode())


def client_for(settings, chat_response):
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/embeddings"):
            return httpx2.Response(200, json=EMBED_OK)
        return chat_response() if callable(chat_response) else chat_response

    http = httpx2.Client(transport=httpx2.MockTransport(handler))
    return LLMClient(settings, http_client=http), seen


def test_every_error_fixture_has_an_expectation():
    assert set(fixture_names()) == set(EXPECTED)


def test_header_files_parse_with_crlf_and_lf(tmp_path):
    for eol in ("\r\n", "\n"):
        p = tmp_path / "h.headers"
        p.write_bytes(f"HTTP/1.1 429 Too Many Requests{eol}retry-after: 60{eol}{eol}".encode())
        assert read_headers(p) == (429, {"retry-after": "60"})


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixture_maps_through_the_client_to_the_expected_category(settings, name):
    cls, category = EXPECTED[name]
    client, seen = client_for(settings, error_response(name))
    with pytest.raises(cls) as info:
        client.chat([{"role": "user", "content": "hi"}])
    assert type(info.value) is cls
    assert ans.categorize(info.value) == category
    assert len(seen) == 1  # no retry, no other endpoint


def test_rate_limit_records_retry_after(settings):
    client, _ = client_for(settings, error_response("rate_limited"))
    with pytest.raises(RateLimitedError) as info:
        client.chat([{"role": "user", "content": "hi"}])
    assert info.value.retry_after == 60.0


def test_success_headers_are_recorded(settings):
    client, _ = client_for(settings, ok_response())
    res = client.chat([{"role": "user", "content": "hi"}], max_tokens=5)
    assert res.resolved_model == "anthropic/claude-haiku-5-5"
    assert res.call_id == "7f63b0d8-3ebe-48cf-873c-467fbf4c327b"
    assert res.cost_usd == 3.5e-06
    assert res.attempted_retries == 0


@pytest.mark.parametrize("status", [400, 404, 418, 422])
def test_unknown_4xx_is_bad_request(settings, status):
    response = httpx2.Response(status, json={"error": {"message": "weird", "type": "other"}})
    client, _ = client_for(settings, response)
    with pytest.raises(RequestRejectedError) as info:
        client.chat([{"role": "user", "content": "hi"}])
    assert ans.categorize(info.value) == "bad_request"


def test_none_header_is_null(settings):
    response = httpx2.Response(
        200,
        headers={"x-litellm-call-id": "none", "x-litellm-response-cost": "None"},
        content=ok_response().content,
    )
    client, _ = client_for(settings, response)
    res = client.chat([{"role": "user", "content": "hi"}])
    assert res.call_id is None and res.cost_usd is None


@pytest.fixture
def threshold_file(tmp_path):
    p = tmp_path / "threshold.toml"
    p.write_text('signal = "rerank_top1"\nthreshold = 0.0621\n')
    return p


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_generate_stage_never_leaks_gateway_text(settings, threshold_file, name):
    _, category = EXPECTED[name]
    _, _, parsed = load_fixture(name)
    client, seen = client_for(settings, error_response(name))
    calls = []

    def factory(s):
        calls.append(s.openai_base_url)
        return client

    rt = ans.build_runtime(
        settings,
        client_factory=factory,
        threshold_path=threshold_file,
        retrieve_fn=FakeRetriever(hits_from([(0.9, "current"), (0.5, "current")])),
        sections_fn=lambda ids, **kw: sections(ids),
        reranker_check=lambda s: None,
    )
    res = ans.answer("What is the limit?", rt, log=False)
    assert calls == [settings.openai_base_url] and len(calls) == 1
    assert {r.url.host for r in seen} == {"gateway.invalid"}
    assert [r.url.path.rsplit("/", 1)[-1] for r in seen] == ["embeddings", "completions"]
    assert settings.openai_base_url == "http://gateway.invalid:4000/v1"
    assert res.n_chat_calls == 1
    if category == "blocked_policy":
        assert res.status == "blocked" and res.sources == []
    else:
        assert (res.status, res.reason) == ("error", category)
        assert len(res.sources) == 2 and res.message.endswith(msg.SFX)
    dump = repr(ans.result_dict(res))
    assert parsed["error"]["message"] not in dump
    for secret in ("ba25d198", "Key Hash", "sk-", "R10", "d6-fixture-budget"):
        assert secret not in dump
