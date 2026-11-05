import pytest

import smoke

pytestmark = pytest.mark.integration


def test_smoke_against_live_setup(capsys):
    """Needs the configured endpoint (gateway or Ollama) and `make up` for Postgres."""
    code = smoke.main(["--pii"])
    out = capsys.readouterr().out
    assert code == 0, out
