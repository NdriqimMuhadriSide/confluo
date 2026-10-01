# Confluo

Modular AI ERP for SMEs. The first module is **CRM**: an AI intake agent that answers
customers across web chat, WhatsApp, email, phone and social, books appointments and builds
customer profiles. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Layout

```
apps/
  api/        FastAPI app (confluo_api): wires core + enabled modules
  worker/     background worker (confluo_worker)
  web/        Next.js + TypeScript + Tailwind + shadcn/ui dashboard
  widget/     embeddable chat widget, built to a single widget.js
packages/
  core/       confluo_core: settings, module registry, db (tenancy, auth, events… to come)
modules/
  crm/        confluo_crm: the CRM module, discovered via the `confluo.modules` entry point
supabase/     Supabase CLI config, migrations and seed for the local stack
infra/        Dockerfiles and docker-compose for the app services
tests/        pytest suite
```

## Prerequisites

- Docker Desktop (running)
- [Supabase CLI](https://supabase.com/docs/guides/local-development/cli/getting-started), [uv](https://docs.astral.sh/uv/), [pnpm](https://pnpm.io), Node 24

On macOS: `brew install supabase/tap/supabase uv pnpm node`

## Setup

```sh
make setup   # uv sync, pnpm install, create .env from .env.example
make dev     # start Supabase, then api, worker, web and widget with hot reload
```

| Service | URL |
|---|---|
| Dashboard | http://localhost:3000 |
| API (OpenAPI docs at `/docs`) | http://127.0.0.1:8100 |
| Widget test page | http://127.0.0.1:3001 |
| Supabase Studio | http://127.0.0.1:54323 |
| Postgres | `postgresql://postgres:postgres@127.0.0.1:54322/postgres` |
| Email testing (Mailpit) | http://127.0.0.1:54324 |

The dashboard home page shows whether the API and database are reachable.

`make up` runs api, worker and web as Docker containers instead (production-like images,
no hot reload). Stop them with `make down` and the database with `make db-stop`.

## Everyday commands

```sh
make check      # lint + typecheck + tests (what CI runs)
make format     # auto-format Python
make db-reset   # recreate the local database
make help       # list all targets
```

## Configuration

All configuration is environment variables, documented in [.env.example](.env.example).
Python reads `CONFLUO_*` variables through `confluo_core.settings.Settings`.
