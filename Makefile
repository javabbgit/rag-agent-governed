.PHONY: setup up up-direct down lint fmt test smoke corpus-check corpus-chunks corpus-prescan db-init ingest ingest-rebuild test-db search retrieval-smoke evalset-check evalset-show eval-pool eval-pool-sheet eval-pool-apply eval-retrieval

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
	uv run pytest -m db

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
	uv run python scripts/eval_retrieval.py --split $(SPLIT) --pool-only

eval-pool-sheet:
	@test -n "$(FILE)" || { echo 'usage: make eval-pool-sheet FILE=reports/eval/pool_dev_<UTC>.csv'; exit 2; }
	uv run python scripts/pool_sheet.py "$(FILE)"

eval-pool-apply:
	@test -n "$(FILE)" || { echo 'usage: make eval-pool-apply FILE=reports/eval/pool_dev_<UTC>_judge.csv'; exit 2; }
	uv run python scripts/apply_pool.py "$(FILE)"

eval-retrieval:
	uv run python scripts/eval_retrieval.py --split $(SPLIT)
