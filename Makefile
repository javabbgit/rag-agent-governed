.PHONY: setup up up-direct down lint fmt test smoke

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
