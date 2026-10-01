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
make migrate    # apply database migrations (make dev does this too)
make db-reset   # recreate the local database and migrate
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

## Multi-tenancy

A **tenant** is one customer business. Every tenant-scoped table has a `tenant_id` and a
Postgres row-level-security policy, so isolation is enforced by the database rather than by
each query:

- Migrations (Alembic, `migrations/` + `packages/core/src/confluo_core/migrations/`) run as
  the owner. The API and worker connect as `confluo_app`, which owns nothing and cannot
  bypass RLS.
- Each request or job runs in one transaction that sets `app.user_id` / `app.tenant_id`
  with `set_config(..., true)`, so the values end with the transaction
  (`confluo_core.tenancy`).
- API endpoints that take `Tenant` (from `confluo_core.deps`) read the `X-Tenant-Id` header,
  check the user is an active member (403 otherwise) and only then enter the tenant.
- Worker jobs use `tenant_transaction(pool, tenant_id)` with the tenant from the job payload.
- New tables: add `tenant_id uuid not null default app.current_tenant_id()`, enable RLS
  and add a policy like `location_tenant`. `tests/tenancy/test_isolation.py` fails for any
  table in `public` without RLS and a policy.

## Roles and permissions

Roles are `owner`, `admin` and `staff`. Permissions are strings declared by core
(`confluo_core.permissions`) and by each module (`ConfluoModule.permissions`, prefixed
with the module key) with the roles that get them. Guard a route with
`Depends(requires("crm.kb.edit"))`; the app refuses to start if a route requires an
undeclared permission. `GET /api/tenant` returns the caller's role and permissions.

Escalation rules are also enforced in the database (migration `0002_rbac`): only owners
and admins change members or invitations, only owners grant or touch the owner role, a
business keeps at least one owner, and members join only via `app.create_tenant()` or
`app.accept_invitation()`. Inside a tenant context only that tenant is visible.

**Invitations:** an admin invites an email with a role. New people get Supabase's invite
email (template `supabase/templates/invite.html`, which links to `/auth/confirm` with a
token hash); people who already have an account see the invitation at their next sign-in.
Either way they accept it in the dashboard, which checks the invitation's email against
their verified token.

## LLM gateway

All model calls go through `confluo_core.llm.LLMGateway` with a *tier*, never a vendor
model: `fast` (intent, extraction), `dialogue` (customer replies) and `embedding`.
`CONFLUO_LLM_FAST`, `CONFLUO_LLM_DIALOGUE` and `CONFLUO_LLM_EMBEDDING` map tiers to
`provider:model` (defaults: Claude Haiku 4.5, Claude Opus 5 with Anthropic's server-side
refusal fallback, Voyage voyage-3.5). Use `fake:<name>` to run offline at no cost. Every
call is logged per tenant in `llm_usage` with an estimated cost (`GET /api/usage`) and
added to the current AI trace. `make smoke-llm` makes one real call per provider once
`ANTHROPIC_API_KEY` / `VOYAGE_API_KEY` are in `.env`.

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
