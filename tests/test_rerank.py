import io
import json
import urllib.error

import pytest

from rag_agent import rerank
from rag_agent.rerank import RerankError, check_pin, percentile, rerank_scores, reranker_info

URL = "http://reranker.invalid:80"
REV = "2cfc18c9415c912f9d8155881c133215df768a70"


class FakeResponse:
    def __init__(self, body, status=200):
        self.status = status
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def patch_urlopen(monkeypatch, result):
    calls = []

    def fake(req, timeout=None):
        calls.append((req, timeout))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(rerank.urllib.request, "urlopen", fake)
    return calls


def test_scores_are_mapped_by_index_and_request_shape(monkeypatch):
    out_of_order = [
        {"index": 2, "score": 0.1},
        {"index": 0, "score": 0.9},
        {"index": 1, "score": 0.5},
    ]
    calls = patch_urlopen(monkeypatch, FakeResponse(out_of_order))
    assert rerank_scores(URL, "q", ["a", "b", "c"], 64, 7.0) == [0.9, 0.5, 0.1]
    req, timeout = calls[0]
    assert req.full_url == URL + "/rerank" and req.get_method() == "POST" and timeout == 7.0
    assert json.loads(req.data) == {
        "query": "q",
        "texts": ["a", "b", "c"],
        "truncate": True,
        "raw_scores": False,
        "return_text": False,
    }


@pytest.mark.parametrize(
    "result",
    [
        urllib.error.HTTPError(URL, 503, "unavailable", {}, io.BytesIO(b"")),
        TimeoutError("slow"),
        urllib.error.URLError("refused"),
        FakeResponse(b"not json"),
        FakeResponse([{"index": 0, "score": 0.5}]),  # index 1 missing
        FakeResponse([{"index": 0, "score": 0.5}, {"index": 0, "score": 0.6}]),  # duplicate
        FakeResponse([{"index": 0, "score": 1.2}, {"index": 1, "score": 0.1}]),
        FakeResponse([{"index": 0, "score": -0.1}, {"index": 1, "score": 0.1}]),
        FakeResponse([{"index": 0, "score": 0.2}, {"index": 5, "score": 0.1}]),
        FakeResponse({"error": "x"}),
    ],
)
def test_rerank_failures_raise(monkeypatch, result):
    patch_urlopen(monkeypatch, result)
    with pytest.raises(RerankError):
        rerank_scores(URL, "q", ["a", "b"], 64, 1.0)


def test_too_many_texts_never_sends(monkeypatch):
    calls = patch_urlopen(monkeypatch, FakeResponse([]))
    with pytest.raises(RerankError, match="batch"):
        rerank_scores(URL, "q", ["a", "b", "c"], 2, 1.0)
    assert calls == []


def test_errors_never_contain_query_or_texts(monkeypatch):
    patch_urlopen(monkeypatch, urllib.error.URLError("refused"))
    with pytest.raises(RerankError) as ei:
        rerank_scores(URL, "SECRET QUERY", ["SECRET TEXT"], 64, 1.0)
    assert "SECRET" not in str(ei.value)


def test_info_returns_known_fields_and_fails_closed(monkeypatch):
    body = {"model_id": "m", "model_sha": REV, "max_input_length": 512, "extra": 1}
    patch_urlopen(monkeypatch, FakeResponse(body))
    info = reranker_info(URL, 5.0)
    assert info["model_sha"] == REV and info["version"] is None and "extra" not in info
    for bad in (FakeResponse(b"x"), FakeResponse([1]), urllib.error.URLError("no")):
        patch_urlopen(monkeypatch, bad)
        with pytest.raises(RerankError):
            reranker_info(URL, 5.0)


def test_check_pin():
    check_pin({"model_sha": REV, "model_id": "BAAI/bge-reranker-base"}, REV)
    check_pin({"model_sha": None, "model_id": f"/data/models--x/snapshots/{REV}"}, REV)
    for info in (
        {"model_sha": "abc", "model_id": "BAAI/bge-reranker-base"},
        {"model_sha": None, "model_id": f"/data/snapshots/{REV}0"},
        {},
    ):
        with pytest.raises(RerankError, match=REV):
            check_pin(info, REV)
    with pytest.raises(RerankError, match="abc") as ei:
        check_pin({"model_sha": "abc", "model_id": "m"}, REV)
    assert "m" in str(ei.value)


def test_percentile_nearest_rank():
    xs = [10.0, 20.0, 30.0, 40.0]
    assert percentile(xs, 50) == 20.0 and percentile(xs, 95) == 40.0
    assert percentile([5.0], 95) == 5.0
