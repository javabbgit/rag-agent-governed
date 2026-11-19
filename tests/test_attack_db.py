"""The attack matrix end to end on the local pgvector (make test-db; run make agent-seed after)."""

import os

import pytest

from rag_agent.agent import mockdata
from rag_agent.agent.db import seed
from rag_agent.agent_attack.attacks import load_attacks
from rag_agent.agent_attack.harness import world
from rag_agent.agent_attack.matrix import pg_deps, run_matrix

pytestmark = pytest.mark.db

DSN = os.environ.get("DATABASE_URL", "")


@pytest.fixture
def fresh():
    if not DSN:
        pytest.skip("DATABASE_URL is not set")
    seed(DSN, mockdata.load_loans(), mockdata.load_contacts(), reset=True)
    yield
    seed(DSN, mockdata.load_loans(), mockdata.load_contacts(), reset=True)


def test_matrix_a01_e02_end_to_end(fresh, settings):
    s = settings.model_copy(update={"database_url": DSN})
    deps = pg_deps(DSN)
    result = run_matrix(load_attacks(), s, deps, world(), only=["A01", "E02"])
    assert result.mismatches == []
    assert result.as_expected == len(result.cells) == 18
    assert result.positives == {"T4": True, "T5": True}
    assert deps.repo.list_requests() == []  # reset at the end
