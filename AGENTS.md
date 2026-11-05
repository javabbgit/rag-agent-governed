# AGENTS.md — rules for coding agents

## What this project is
RAG + tool-calling agent over fictional Demo Mortgage Co. documents.
By default every model call goes through llm-gateway-governance (LiteLLM proxy on localhost:4000),
which provides PII detection, residency routing, budgets and audit. A direct mode can point at any
OpenAI-compatible endpoint. The human owner does design, review, testing and governance;
agents write code only within these rules.

## Hard rules
1. Scope: do not add multi-agent, GraphRAG, fine-tuning, multi-tenancy, production auth,
   or front-end polish. Out-of-scope ideas go in docs/ideas-v1.1.md, not in code.
2. Dependencies: do not add or upgrade a dependency without asking. No LangChain, LlamaIndex,
   CrewAI, AutoGen or other agent/RAG frameworks.
3. One entry point for models: only src/rag_agent/llm_client.py may construct an OpenAI client.
   Every chat and embedding call goes through it.
4. Fail closed: in gateway mode, never fall back to direct mode or any other endpoint when the
   gateway errors or is unreachable. Never retry a policy, auth, allow-list or budget error.
5. No secrets in the repo: read keys and URLs from environment only. Never print a full key;
   mask it. Never commit .env or .env.* (except .env.example).
6. No real or realistic PII anywhere (corpus, tests, fixtures, examples, logs).
   Loan numbers use the format DMC-YYYY-NNNNN. Never write 9-digit numbers.
   The single exception is the PII_PROBE_TEXT constant in scripts/smoke.py
   (the gateway's FAKE_SIN 193 456 787, Luhn-valid, not anyone's number), used only for the PII probe.
7. Side effects (later milestones): tools with side effects must stop in a pending-approval state,
   enforced in code by the policy layer, never by prompt wording.
8. Evaluation integrity (later milestones): never edit gold labels, eval datasets or thresholds
   to make a result pass. Tune on dev, report on test. Do not rely on temperature for determinism:
   chat-cloud accepts no sampling params (the gateway drops them, gateway decision 20). Record the resolved
   model and the sampling params actually sent; run each eval item N times and report the spread.
9. Do not modify anything outside this repository (in particular the gateway repository).
10. Do not commit, tag or push. Leave changes in the working tree for human review.

## Commands
- make setup   install with uv (Python 3.11)
- make up      start pgvector (docker compose)
- make up-direct  also start the bundled Ollama for direct mode (profile "direct")
- make down    stop it
- make lint    ruff check + ruff format --check
- make test    unit tests only; must pass with no network and no gateway
- make smoke   connectivity test against the configured endpoint (writes reports/smoke/*.json)

## Testing requirements
- Every new module gets unit tests. Unit tests never touch the network.
- Tests that need the gateway, Ollama or Postgres are marked @pytest.mark.integration
  and are excluded from make test.
- Gateway error handling is tested against the recorded fixtures in tests/fixtures/gateway_errors/.

## Ask before
Changing the database schema, docker-compose.yml, environment variables, the error taxonomy,
the eval datasets, or this file.

## Definition of done for a task
- make lint and make test pass.
- No new warnings, no TODOs without an issue reference.
- README or docs updated if behaviour or configuration changed.
- Reply with: files changed, what was verified and how, open questions.
