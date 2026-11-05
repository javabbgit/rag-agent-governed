# rag-agent-governed

A RAG + tool-calling agent over fictional Demo Mortgage Co. documents. By default every model
call goes through llm-gateway-governance (a LiteLLM proxy on localhost:4000) for PII detection,
residency routing, budgets and audit; a direct mode can target any OpenAI-compatible endpoint.

**Status: work in progress.** D1 only: skeleton, configuration, a single fail-closed LLM client
and a connectivity smoke test. No retrieval, agents or UI yet. Agent rules are in AGENTS.md.

## Commands
- `make setup` install with uv (Python 3.11)
- `make up` start pgvector (docker compose)
- `make up-direct` also start the bundled Ollama for direct mode (embeddings only; direct-mode chat goes to a cloud OpenAI-compatible endpoint)
- `make down` stop everything
- `make lint` ruff check + format check; `make fmt` auto-fix
- `make test` unit tests only (no network, no gateway)
- `make smoke` connectivity test against the configured endpoint (writes `reports/smoke/*.json`);
  options: `uv run python scripts/smoke.py --env-file PATH --only chat -v`

Configuration: copy `.env.example` to `.env`.
