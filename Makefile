.DEFAULT_GOAL := help
.PHONY: help setup db db-stop db-reset dev up down lint format typecheck test check

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Install Python and Node dependencies, create .env
	uv sync
	pnpm install
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example")

db: ## Start the Supabase local stack (Postgres, Auth, Studio) in Docker
	supabase start

db-stop: ## Stop the Supabase local stack
	supabase stop

db-reset: ## Recreate the local database and re-run migrations + seed
	supabase db reset

dev: db ## Start db, then api, worker, web and widget with hot reload
	uv run honcho -f Procfile.dev start

up: db ## Start db, then api, worker and web as Docker containers
	docker compose -f infra/docker-compose.yml up --build

down: ## Stop the app containers
	docker compose -f infra/docker-compose.yml down

lint: ## Ruff + ESLint
	uv run ruff check .
	uv run ruff format --check .
	pnpm -r lint

format: ## Auto-format Python
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## mypy + tsc
	uv run mypy packages modules apps/api apps/worker tests
	pnpm -r typecheck

test: ## pytest
	uv run pytest

check: lint typecheck test ## Everything CI runs
