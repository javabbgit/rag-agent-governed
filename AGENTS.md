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
   Corpus text under corpus/docs/ is English, ASCII only, and contains no person names,
   phone numbers, emails, street addresses, postal codes, or digit runs of 9 or more.
7. Side effects (later milestones): tools with side effects must stop at an `awaiting_raise` draft that a human raises to `pending_approval`,
   enforced in code by the policy layer, never by prompt wording.
8. Evaluation integrity (later milestones): never edit gold labels, eval datasets or thresholds
   to make a result pass. Tune on dev, report on test. Do not rely on temperature for determinism:
   chat-cloud accepts no sampling params (the gateway drops them, gateway decision 20). Record the resolved
   model and the sampling params actually sent; run each eval item N times and report the spread.
9. Do not modify anything outside this repository (in particular the gateway repository).
10. Do not commit, tag or push. Leave changes in the working tree for human review.
11. corpus/facts.toml is the single source of truth for policy numbers (ratios, rates, fees,
    limits). Code that needs one reads it from there; never hard-code a policy number.
12. All retrieval goes through rag_agent.retrieve.retrieve(). Tools, UI and eval code never
    query the chunks table directly.
13. Retrieval parameters (tokenizer, stopwords, BM25 k1/b, RRF k, candidate depth) live in one
    constant block in retrieve.py. Changing any of them needs a full eval run and a decision record.
14. Every embedding input carries the nomic task prefix: "search_document: " for chunks,
    "search_query: " for queries.
15. eval/retrieval/questions.toml is human-owned. Agents never edit the test split. After tag
    d4-eval a test label changes only for a proven labeling error, made by the human with a
    decision record.
16. Tuning (reranking, thresholds, retrieval parameters, prompts) is judged on the dev split only.
    Test runs need a clean tree and append to eval/retrieval/test_runs.csv; never edit or delete
    a line of that file.
17. Retrieval metrics come from src/rag_agent/metrics.py only. Scripts never re-implement
    Hit@k, MRR or the bootstrap.        

## Commands
- make setup   install with uv (Python 3.11)
- make up      start pgvector (docker compose)
- make up-direct  also start the bundled Ollama for direct mode (profile "direct")
- make down    stop it
- make lint    ruff check + ruff format --check
- make test    unit tests only; must pass with no network and no gateway
- make smoke   connectivity test against the configured endpoint (writes reports/smoke/*.json)
- make corpus-check    validate corpus format, PII patterns, numbers and banned terms
- make corpus-chunks   write reports/corpus/chunks.csv (frozen chunk IDs)
- make corpus-prescan  send every chunk through the gateway's rag-embed (gateway mode only)
- make db-init create tables (idempotent)
- make ingest embed and store the corpus; skips unchanged chunks (COLLECTION=main)
- make ingest-rebuild re-embed every chunk
- make search Q="..." MODE=hybrid|bm25|vector
- make retrieval-smoke 12 sanity queries x 3 modes; not part of any eval set
- make test-db         db-marked tests against the local pgvector
- make evalset-check [ALLOW_INCOMPLETE=1|FINAL=1]   validate eval/retrieval/questions.toml
- make evalset-show ID=T07 | CHUNK=<chunk_id>       a question with its gold text, or one chunk
- make eval-pool SPLIT=dev|test                     unjudged top-3 chunks; prints no metrics
- make eval-retrieval SPLIT=dev|test                Hit@1/3/5 and MRR@10 with 95% CI per mode

## Testing requirements
- Every new module gets unit tests. Unit tests never touch the network.
- Tests that need only the local Postgres are marked @pytest.mark.db and run with
  make test-db. Tests that need the gateway or Ollama are marked
  @pytest.mark.integration. Both are excluded from make test.
- Gateway error handling is tested against the recorded fixtures in tests/fixtures/gateway_errors/.

## Ask before
Changing the database schema, docker-compose.yml, environment variables, the error taxonomy,
the eval datasets, or this file.
Changing corpus/facts.toml, corpus/manifest.toml or anything under corpus/docs/.
Chunk IDs are frozen after tag d2-corpus; renaming a heading invalidates eval labels.
Changing db/schema.sql, the embedding text format, or any retrieval parameter.
Changing eval/retrieval/questions.toml, eval/retrieval/test_runs.csv or src/rag_agent/metrics.py.

## Definition of done for a task
- make lint and make test pass.
- No new warnings, no TODOs without an issue reference.
- README or docs updated if behaviour or configuration changed.
- Reply with: files changed, what was verified and how, open questions.

## Reranking and fallback (D5)
18. Reranking runs only on hybrid candidates, after the gateway accepted the query embedding.
    Never send text to the reranker for a query the gateway rejected.
19. Reranker failure is fail-closed in scripts: preflight before any gateway request, exit 1,
    no metrics, no ledger line. Never fall back to hybrid results silently.
20. eval/fallback/threshold.toml is human-owned and is committed before any test run scored
    with it. eval/fallback/test_runs.csv is append-only.
Commands: make reranker-check | make eval-rerank-sweep SPLIT=dev |
  make eval-retrieval / eval-pool SPLIT=.. [MODES=..] [RERANK_N=..] |
  make eval-fallback SPLIT=dev|test REPORT=path [WRITE_THRESHOLD=1]
Ask before changing: docker-compose.yml image pins, the reranker revision, RERANK_CANDIDATES,
eval/fallback/threshold.toml.

21. Answers to `make ask` go only through src/rag_agent/answer.py; agent chat goes only through src/rag_agent/agent/loop.py and LLMClient.chat_tools. In gateway mode never fall back to the direct
    endpoint or to another model alias on any error; map gateway errors through the fixture-driven
    classifier and return the fixed messages in messages.py. Never show raw gateway error text or keys.
22. Citations are validated in code (citations.py). Do not relax validation, add retries, or change
    the fallback threshold, rerank N or context size to raise the answered rate. Prompt changes need a
    new prompts/answer_vN.md file, a full dev eval-answer run, and a decision-log entry.
23. Answer logs never store interactive question or answer text (sha256 only); tests write logs to
    tmp_path only. eval-answer runs on dev; the test split needs --final and is run once in D10.
    D6 commands: make ask Q="...", make eval-answer SPLIT=dev REPEATS=3, make answer-review-tally FILE=...
24. Never edit src/rag_agent/agent/tool_specs.json without saying so; never create or regenerate
    tools.lock or run `make tools-lock`. The human regenerates the lock in its own commit after review.
25. Only approvals.py (raise, discard, approve, refuse) may move an exception request out of
    `awaiting_raise`; the agent can only prepare drafts. The single exception is
    tools.execute_unguarded when approval_gate is off, and the database rejects it. Agent chat goes
    only through src/rag_agent/agent/loop.py and LLMClient.chat_tools (max_retries=0): no retry in
    the loop and no client built in the loop. Never add a code path that sends messages, emails or
    HTTP requests to external systems. Every tool call goes through policy.py.
26. Policy numbers come only from corpus/facts.toml; agent limits and defense switches come only from
    config/agent_policy.toml. Never hard-code either.
27. Mock data in data/mock/ must stay free of names, phone numbers, emails, street addresses, postal
    codes and runs of 9 or more digits. Defenses may be switched off only with
    AGENT_DEFENSES_OFF_ACK=1, in tests, governance drills or D9 runs, never in demos. make test-db
    resets the agent tables; run make agent-seed afterwards.
    D7 commands: make agent Q="..." AS=<user_id>, make agent-smoke REPEATS=2, make agent-audit
    [SINCE=..], make exceptions / raise / discard / approve / refuse ID=.. AS=.., make agent-seed
    [RESET=1], make agent-pick WHO=.. [KIND=..]; make tools-lock is for the human only.
28. eval/agent/tasks.toml is human-owned: agents never edit it. Its gold values are literals that
    were computed independently of the code; after any change to it, `make agent-eval-check` and
    `make test` must both pass before any agent evaluation. The test split needs FINAL=1 and a
    clean tree, and every test run appends one line to eval/agent/test_runs.csv; never edit or
    delete a line of that file.
29. Agent evaluation goes only through scripts/agent_eval.py. [policy] checks read database facts
    and the tool trace, never model wording, and must be 100%: a failure is a bug to fix in the
    code, never a check to relax. Never move a check between policy and behaviour, change a task,
    a phrase list or a check after a test run to raise a score; a checker fix after a test run
    needs a contradiction with the written spec, a rescore of every report, both sets of numbers
    and a decision record. Phrase lists match whole words, so a list names every inflection it
    needs (duplicate, duplicates, duplicated). Agent prompts change only as a new
    prompts/agent_vN.md with a full dev run; the same prompt is used for every model.
30. LLM_MAX_RETRIES defaults to 0 and LLM_TIMEOUT_S stays above the gateway's own model timeout:
    never add SDK retries or retries in the agent loop. The only rerun is the evaluator's single
    whole-run rerun after a gateway error, and it is logged. make agent-eval empties
    agent.exception_requests and agent.drafts before every run and never touches
    agent.policy_events. The gateway may redact PII (R5) inside model-written tool arguments
    during a run; such runs are kept, and the audit counts them per run and reports the
    pass rate without them as a sensitivity check.
    D8 commands: make agent-eval-check, make agent-eval SPLIT=dev|test [FINAL=1] [MODEL=..]
    [COST_CAP=..] [ONLY=..] [PACE=..], make agent-eval-prescan, make agent-eval-tally FILE=..,
    make agent-eval-rescore REPORT=.., make agent-eval-compare A=.. B=..