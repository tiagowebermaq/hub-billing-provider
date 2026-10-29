# Ubuntu / Linux. No Windows use os scripts em scripts\*.ps1.
.PHONY: install test test-docker lint up down logs api po

install:
	python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

test:
	.venv/bin/pytest --cov --cov-report=term-missing --cov-fail-under=80

test-docker:
	docker compose --profile test run --rm --build tests

lint:
	.venv/bin/ruff check src tests tools && .venv/bin/mypy src

up:
	test -f .env || cp .env.example .env
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api

api:
	.venv/bin/uvicorn hub_billing.api.app:create_app --factory --reload --port 8010

po:
	.venv/bin/python tools/build_po.py $(ARGS)
