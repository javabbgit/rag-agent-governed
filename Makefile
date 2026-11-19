.PHONY: setup up up-direct down lint fmt test smoke corpus-check corpus-chunks corpus-prescan db-init ingest ingest-rebuild test-db search retrieval-smoke evalset-check evalset-show eval-pool eval-pool-sheet eval-pool-apply eval-retrieval reranker-check eval-rerank-sweep eval-fallback ask eval-answer answer-review-tally tools-lock agent-seed agent-db-check agent-data-report agent-pick agent-prescan exceptions raise discard approve refuse agent agent-smoke agent-audit agent-eval-check agent-eval agent-eval-prescan agent-eval-tally agent-eval-rescore agent-eval-compare attack-check attack-matrix attack-delivery attack-prescan attack-eval attack-rescore attack-compare attack-denials

setup:
	uv sync --python 3.11

up:
	docker compose up -d db

up-direct:
	docker compose --profile direct up -d

down:
	docker compose --profile direct down

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff check --fix .
	uv run ruff format .

test:
	uv run pytest

smoke:
	uv run python scripts/smoke.py --pii

corpus-check:
	uv run python scripts/check_corpus.py $(if $(ALLOW_MISSING),--allow-missing)

corpus-chunks:
	uv run python scripts/check_corpus.py --list-chunks $(if $(ALLOW_MISSING),--allow-missing)

corpus-prescan:
	uv run python scripts/pii_prescan.py $(if $(DOC),--doc $(DOC))

COLLECTION ?= main
DOCS_DIR ?= corpus/docs

db-init:
	uv run python -c "from rag_agent.config import load_settings; from rag_agent.store import init_schema; init_schema(load_settings().database_url)"

ingest:
	uv run python scripts/ingest.py --collection $(COLLECTION) --docs-dir $(DOCS_DIR)

ingest-rebuild:
	uv run python scripts/ingest.py --collection $(COLLECTION) --docs-dir $(DOCS_DIR) --rebuild

test-db:
	DATABASE_URL="$${DATABASE_URL:-$$(uv run python -c 'from rag_agent.config import load_settings; print(load_settings().database_url)')}" uv run pytest -m db

MODE ?= hybrid
K ?= 10

search:
	@test -n "$(Q)" || { echo 'usage: make search Q="your question" [MODE=hybrid|bm25|vector] [K=10]'; exit 2; }
	uv run python scripts/search.py "$(Q)" --mode $(MODE) -k $(K)

retrieval-smoke:
	uv run python scripts/retrieval_smoke.py

SPLIT ?= dev

evalset-check:
	uv run python scripts/check_evalset.py $(if $(ALLOW_INCOMPLETE),--allow-incomplete) $(if $(FINAL),--final)

evalset-show:
	@test -n "$(ID)$(CHUNK)" || { echo 'usage: make evalset-show ID=D01   or   CHUNK=POL-003#slug'; exit 2; }
	uv run python scripts/show_question.py $(if $(ID),--id $(ID)) $(if $(CHUNK),--chunk '$(CHUNK)')

eval-pool:
	uv run python scripts/eval_retrieval.py --split $(SPLIT) --pool-only $(if $(MODES),--modes $(MODES)) $(if $(RERANK_N),--rerank-n $(RERANK_N))

eval-pool-sheet:
	@test -n "$(FILE)" || { echo 'usage: make eval-pool-sheet FILE=reports/eval/pool_dev_<UTC>.csv'; exit 2; }
	uv run python scripts/pool_sheet.py "$(FILE)"

eval-pool-apply:
	@test -n "$(FILE)" || { echo 'usage: make eval-pool-apply FILE=reports/eval/pool_dev_<UTC>_judge.csv'; exit 2; }
	uv run python scripts/apply_pool.py "$(FILE)"

eval-retrieval:
	uv run python scripts/eval_retrieval.py --split $(SPLIT) $(if $(MODES),--modes $(MODES)) $(if $(RERANK_N),--rerank-n $(RERANK_N))

reranker-check:
	uv run python scripts/reranker_check.py

eval-rerank-sweep:
	uv run python scripts/eval_rerank_sweep.py --split $(SPLIT)

eval-fallback:
	@test -n "$(REPORT)" || { echo 'usage: make eval-fallback SPLIT=dev|test REPORT=reports/eval/retrieval_<split>_<UTC>.json [WRITE_THRESHOLD=1]'; exit 2; }
	uv run python scripts/eval_fallback.py --split $(SPLIT) --report "$(REPORT)" $(if $(WRITE_THRESHOLD),--write-threshold)

REPEATS ?= 3

ask:
	@test -n "$(Q)" || { echo 'usage: make ask Q="your question"'; exit 2; }
	uv run python scripts/ask.py $(if $(JSON),--json) "$(Q)"

eval-answer:
	uv run python scripts/eval_answer.py --split $(SPLIT) --repeats $(REPEATS) $(if $(FINAL),--final)

answer-review-tally:
	@test -n "$(FILE)" || { echo 'usage: make answer-review-tally FILE=reports/eval/answer_review_dev_<UTC>.csv'; exit 2; }
	uv run python scripts/answer_review_tally.py "$(FILE)"

tools-lock:
	uv run python scripts/tools_lock.py

agent-seed:
	uv run python scripts/agent_seed.py $(if $(RESET),--reset)

agent-db-check:
	uv run python scripts/agent_db_check.py

agent-data-report:
	uv run python scripts/agent_data_report.py

agent-pick:
	@test -n "$(WHO)" || { echo 'usage: make agent-pick WHO=uw-01 [KIND=tds_minor|tds_major]'; exit 2; }
	uv run python scripts/agent_pick.py $(WHO) $(if $(KIND),--kind $(KIND))

agent-prescan:
	uv run python scripts/agent_prescan.py

exceptions:
	uv run python scripts/exceptions_cli.py list $(if $(STATUS),--status $(STATUS)) $(if $(IDS_ONLY),--ids-only)

raise:
	@test -n "$(ID)" -a -n "$(AS)" || { echo 'usage: make raise ID=ER-000001 AS=uw-01 [YES=1]'; exit 2; }
	uv run python scripts/exceptions_cli.py raise $(ID) --as $(AS) $(if $(YES),--yes)

discard:
	@test -n "$(ID)" -a -n "$(AS)" -a -n "$(REASON)" || { echo 'usage: make discard ID=ER-000001 AS=uw-01 REASON="..." [YES=1]'; exit 2; }
	uv run python scripts/exceptions_cli.py discard $(ID) --as $(AS) --reason "$(REASON)" $(if $(YES),--yes)

approve:
	@test -n "$(ID)" -a -n "$(AS)" || { echo 'usage: make approve ID=ER-000001 AS=su-01 [YES=1]'; exit 2; }
	uv run python scripts/exceptions_cli.py approve $(ID) --as $(AS) $(if $(YES),--yes)

refuse:
	@test -n "$(ID)" -a -n "$(AS)" -a -n "$(REASON)" || { echo 'usage: make refuse ID=ER-000001 AS=su-01 REASON="..." [YES=1]'; exit 2; }
	uv run python scripts/exceptions_cli.py refuse $(ID) --as $(AS) --reason "$(REASON)" $(if $(YES),--yes)

# AS is a GNU make built-in (default "as"), so the usage check looks at where it was set.
agent:
	@test -n "$(Q)" -a "$(origin AS)" != "default" || { echo 'usage: make agent Q="your question" AS=uw-01 [JSON=1]'; exit 2; }
	uv run python scripts/agent.py $(if $(JSON),--json) --as $(AS) "$(Q)"

agent-smoke:
	uv run python scripts/agent_smoke.py --repeats $(REPEATS)

agent-audit:
	uv run python scripts/agent_audit.py $(if $(SINCE),--since "$(SINCE)")

agent-eval-check:
	uv run python scripts/agent_eval.py check

# Sends gateway requests. The test split needs FINAL=1 and a clean tree.
agent-eval:
	uv run python scripts/agent_eval.py run --split $(SPLIT) --repeats $(REPEATS) $(if $(MODEL),--model $(MODEL)) $(if $(FINAL),--final) $(if $(COST_CAP),--cost-cap $(COST_CAP)) $(if $(ONLY),--only $(ONLY)) $(if $(PACE),--pace $(PACE))

agent-eval-prescan:
	uv run python scripts/agent_eval.py prescan

agent-eval-tally:
	@test -n "$(FILE)" || { echo 'usage: make agent-eval-tally FILE=reports/agent/review_....csv|failures_....csv'; exit 2; }
	uv run python scripts/agent_eval.py tally "$(FILE)"

agent-eval-rescore:
	@test -n "$(REPORT)" || { echo 'usage: make agent-eval-rescore REPORT=reports/agent/eval_....json [ALLOW_NEW_TASKS=1]'; exit 2; }
	uv run python scripts/agent_eval.py rescore "$(REPORT)" $(if $(ALLOW_NEW_TASKS),--allow-new-tasks)

agent-eval-compare:
	@test -n "$(A)" -a -n "$(B)" || { echo 'usage: make agent-eval-compare A=reports/agent/eval_a.json B=reports/agent/eval_b.json'; exit 2; }
	uv run python scripts/agent_eval.py compare "$(A)" "$(B)"

# D9 attack set: validation only; no writes, no requests.
attack-check:
	uv run python scripts/attack_eval.py check

# Scripted runs against the local database; no model calls.
attack-matrix:
	uv run python scripts/attack_eval.py matrix

# Sends gateway requests (rag-embed only).
attack-delivery:
	uv run python scripts/attack_eval.py delivery

# Sends gateway requests (chat-cloud, max_tokens 16).
attack-prescan:
	uv run python scripts/attack_eval.py prescan

# Sends gateway requests (chat-cloud and rag-embed). MODE is on or off; the default of MODE
# (hybrid, for make search) is refused. FINAL=1 needs a clean tree and REPEATS=3.
attack-eval:
	@test "$(MODE)" = on -o "$(MODE)" = off || { echo 'usage: make attack-eval MODE=on|off COST_CAP=<usd> [REPEATS=3] [FINAL=1] [ONLY=A01,B01]'; exit 2; }
	uv run python scripts/attack_eval.py run --mode $(MODE) --repeats $(or $(REPEATS),3) $(if $(FINAL),--final) $(if $(COST_CAP),--cost-cap $(COST_CAP)) $(if $(ONLY),--only $(ONLY))

attack-rescore:
	@test -n "$(REPORT)" || { echo 'usage: make attack-rescore REPORT=reports/agent/attack_on_....json [R5=calls.csv]'; exit 2; }
	uv run python scripts/attack_eval.py rescore "$(REPORT)" $(if $(R5),--r5 "$(R5)")

attack-compare:
	@test -n "$(A)" -a -n "$(B)" || { echo 'usage: make attack-compare A=reports/agent/attack_on_....json B=reports/agent/attack_off_....json'; exit 2; }
	uv run python scripts/attack_eval.py compare "$(A)" "$(B)"

attack-denials:
	@test -n "$(REPORTS)" || { echo 'usage: make attack-denials REPORTS="reports/agent/eval_a.json reports/agent/eval_b.json"'; exit 2; }
	uv run python scripts/attack_eval.py denials $(REPORTS)
