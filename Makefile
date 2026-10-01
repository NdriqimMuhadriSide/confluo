.DEFAULT_GOAL := help

# Secret key of the running Supabase *local* stack, read at run time so it is never
# committed. Used by the API for invitation emails and by the e2e tests.
LOCAL_SECRET_KEY = $$(supabase status -o env 2>/dev/null | sed -n 's/^SECRET_KEY="\{0,1\}\([^"]*\)"\{0,1\}$$/\1/p')
.PHONY: help setup db db-stop db-reset migrate dev up down lint format typecheck test e2e api-client erd seed check

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

db-reset: ## Recreate the local database and re-run migrations
	supabase db reset
	$(MAKE) migrate

migrate: ## Apply database migrations (Alembic) and enable the app role
	uv run alembic upgrade heads
	uv run python scripts/set_app_role_password.py

dev: db migrate ## Start db, migrate, then api, worker, web and widget with hot reload
	CONFLUO_SUPABASE_SECRET_KEY=$${CONFLUO_SUPABASE_SECRET_KEY:-$(LOCAL_SECRET_KEY)} \
		uv run honcho -f Procfile.dev start

up: db migrate ## Start db, migrate, then api, worker and web as Docker containers
	CONFLUO_SUPABASE_SECRET_KEY=$${CONFLUO_SUPABASE_SECRET_KEY:-$(LOCAL_SECRET_KEY)} \
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
	uv run mypy packages modules apps/api apps/worker tests scripts
	pnpm -r typecheck

# Database tests run against the Supabase local stack; start it with `make db`.
CONFLUO_TEST_DATABASE_URL ?= postgresql://postgres:postgres@127.0.0.1:54322/postgres
export CONFLUO_TEST_DATABASE_URL

test: ## pytest (database tests need `make db`)
	uv run pytest

e2e: ## Browser tests (auth, members/roles) against the running stack (`make dev` first)
	pnpm --filter @confluo/e2e exec playwright install chromium
	SUPABASE_LOCAL_SECRET_KEY=$(LOCAL_SECRET_KEY) pnpm --filter @confluo/e2e all

seed: ## Create or reset the demo tenant (login demo@confluo.local / demo-confluo-2026)
	SUPABASE_LOCAL_SECRET_KEY=$(LOCAL_SECRET_KEY) uv run python scripts/seed_demo.py

erd: ## Regenerate docs/ERD.md from the migrated local database
	uv run python scripts/generate_erd.py

api-client: ## Regenerate openapi.json and the dashboard's TypeScript client
	uv run python scripts/export_openapi.py
	pnpm --filter @confluo/web gen:api

check: lint typecheck test ## Everything CI runs
