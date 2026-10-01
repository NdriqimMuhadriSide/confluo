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

Open the dashboard, create an account at `/signup` and confirm it from the email in
Mailpit. The home page then shows whether the API, the database and your session work.

`make up` runs api, worker and web as Docker containers instead (production-like images,
no hot reload). Stop them with `make down` and the database with `make db-stop`.

## Everyday commands

```sh
make check      # lint + typecheck + tests (what CI runs; db tests need `make db`)
make format     # auto-format Python
make api-client # after changing API routes or models: regenerate the dashboard's TS client
make e2e        # browser test of sign-up / sign-in (needs `make dev` running)
make db-reset   # recreate the local database
make help       # list all targets
```

## Authentication

Staff sign in with Supabase Auth (email + password, or a magic link). The dashboard keeps
the session in cookies via `@supabase/ssr`; `src/proxy.ts` refreshes it and sends
signed-out visitors to `/login`. Every API call carries the user's access token, and the
API verifies it locally against the project's public signing keys (ES256, JWKS) with the
`current_user` dependency in `confluo_core.auth`. All module routes require it.

`make e2e` runs a browser test of the whole flow (sign-up, confirmation, password and
magic-link sign-in, sign-out, refusals) against a running `make dev` or `make up` stack.

Cloud project: `tjflufbcqjfqnblbdfma` (Ireland, eu-west-1). The Data API is disabled, so the
publishable key only works for Auth.

## CI

GitHub Actions ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs on every push to
`main` and every pull request:

- **Python**: ruff, mypy (strict) and pytest against a Postgres 17 service container
- **Web**: eslint, tsc and production builds of the dashboard and widget
- **API client drift**: regenerates `apps/web/openapi.json` and `src/lib/api/schema.d.ts`
  and fails if they differ from what is committed

## Configuration

All configuration is environment variables, documented in [.env.example](.env.example).
Python reads `CONFLUO_*` variables through `confluo_core.settings.Settings`.
