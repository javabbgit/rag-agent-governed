import pytest

import ask
from answer_fakes import FakeClient, FakeRetriever, hits_from, make_runtime
from rag_agent import messages as msg


@pytest.fixture
def fake_rt(monkeypatch, settings):
    client = FakeClient(chat="unused")
    rt = make_runtime(settings, client, FakeRetriever(hits_from([(0.9, "current")])))
    monkeypatch.setattr(ask, "load_settings", lambda *a, **k: settings)
    monkeypatch.setattr(ask, "build_runtime", lambda s: rt)
    return client


@pytest.mark.parametrize("question", ["", "   ", "\t\n"])
def test_blank_question_is_rejected_input_with_exit_0(fake_rt, capsys, question):
    assert ask.main([question]) == 0
    out = capsys.readouterr().out
    assert "rejected_input" in out and "empty" in out and msg.B11a in out
    assert fake_rt.embeds == [] and fake_rt.chats == []


def test_missing_question_is_a_usage_error(fake_rt):
    assert ask.main([]) == 2
