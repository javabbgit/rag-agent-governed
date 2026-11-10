"""Unit tests for scripts/pii_prescan.py (fake client, no network)."""

import json
from pathlib import Path

import pii_prescan
from rag_agent.config import Settings
from rag_agent.errors import PolicyBlockedError

FIX = Path(__file__).parent / "fixtures" / "corpus" / "docs"


class FakeClient:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def embed(self, texts):
        self.calls.append(texts)
        if self.fail_on and self.fail_on in texts[0]:
            raise PolicyBlockedError("policy_blocked: rule R10", 403)


def chunks():
    from rag_agent.corpus import iter_chunks

    return list(iter_chunks(FIX))


def test_one_chunk_per_request_sequential_with_pause():
    client, pauses = FakeClient(), []
    res = pii_prescan.prescan(client, chunks(), sleep=pauses.append)
    assert all(len(c) == 1 for c in client.calls)
    assert [c[0] for c in client.calls] == [c.text for c in chunks()]
    assert pauses == [0.2] * (len(res) - 1)
    assert all(r["ok"] and r["error_class"] is None for r in res)


def test_errors_counted_and_text_not_recorded():
    client = FakeClient(fail_on="Scope of Review")
    res = pii_prescan.prescan(client, chunks(), sleep=lambda _s: None)
    bad = [r for r in res if not r["ok"]]
    assert bad and bad[0]["error_class"] == "PolicyBlockedError"
    blob = json.dumps(res)
    assert "Scope of Review" not in blob and "Each step is recorded" not in blob
    assert set(res[0]) == {"chunk_id", "sent_utc", "latency_ms", "ok", "error_class"}


def test_refuses_outside_gateway_mode(monkeypatch, capsys):
    s = Settings(
        _env_file=None,
        llm_mode="direct",
        openai_base_url="http://x.invalid/v1",
        openai_api_key="k",
        chat_model="c",
        embed_model="e",
        embed_dim=4,
    )  # type: ignore[call-arg]
    monkeypatch.setattr(pii_prescan, "load_settings", lambda _f=None: s)
    assert pii_prescan.main([]) == 1
    assert "gateway" in capsys.readouterr().out
