import json

import pytest

from agent_prescan import prescan, records, run
from rag_agent.agent import mockdata


class FakeClient:
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    def chat(self, messages, **kw):
        if self.fail_at == len(self.calls):
            raise RuntimeError("blocked")
        self.calls.append((messages, kw))


def test_one_request_per_record_with_max_tokens_16():
    client = FakeClient()
    sleeps = []
    n = prescan(client, records(), pause=0, sleep=sleeps.append)
    expected = len(mockdata.load_loans()) + len(mockdata.load_contacts())
    assert n == len(client.calls) == expected
    messages, kw = client.calls[0]
    assert kw == {"max_tokens": 16} and messages[0]["role"] == "user"
    assert json.loads(messages[0]["content"])["loan_number"]


def test_stops_on_first_error_without_retry():
    client = FakeClient(fail_at=2)
    with pytest.raises(RuntimeError):
        prescan(client, records(), pause=0, sleep=lambda _: None)
    assert len(client.calls) == 2


def test_gateway_mode_only(settings, capsys):
    direct = settings.model_copy(update={"llm_mode": "direct"})
    client = FakeClient()
    assert run(direct, client) == 1 and client.calls == []
    assert "gateway mode only" in capsys.readouterr().err


def test_error_output_has_no_record_text(settings, capsys, monkeypatch):
    monkeypatch.setattr("agent_prescan.time.sleep", lambda _: None)
    assert run(settings, FakeClient(fail_at=0)) == 1
    err = capsys.readouterr().err
    assert "DMC-" not in err and "blocked" not in err
