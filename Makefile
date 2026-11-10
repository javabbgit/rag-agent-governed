.PHONY: setup up up-direct down lint fmt test smoke corpus-check corpus-chunks corpus-prescan

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
