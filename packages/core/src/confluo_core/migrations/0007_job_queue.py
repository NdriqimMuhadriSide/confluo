"""Job queue (Procrastinate) and the webhook ledger's system functions.

Procrastinate lives in its own schema `procrastinate`, so `public` keeps the rule
"every table has RLS". Its schema SQL is vendored (sql/procrastinate_3_10_0_schema.sql)
so this migration doesn't change when the library is upgraded; upgrades get their
own migration from Procrastinate's migration files. confluo_app reaches it through
its role search_path.

Webhooks arrive before the tenant is known, so the ledger is written and processed
through SECURITY DEFINER functions rather than RLS policies:

- app.record_inbound_event(...)  insert, or NULL when (provider, external_id) exists
- app.start_inbound_event(id)    mark processing, count the attempt, return the row
- app.finish_inbound_event(...)  record outcome and the resolved tenant

`job_error` keeps the error of each failed attempt per tenant (Procrastinate stores
no messages); System health lists a tenant's dead jobs from it.

`app.audit_event(...)` audits actions that aren't row changes (e.g. retrying a job),
with the same actor/IP rules as the audit trigger.

Revision ID: 0007_job_queue
Revises: 0006_audit
Create Date: 2026-10-01
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0007_job_queue"
down_revision: str | Sequence[str] | None = "0006_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_SQL = Path(__file__).parent / "sql" / "procrastinate_3_10_0_schema.sql"

# A log of failures, not business data (see test_every_table_has_an_audit_trigger).
NOT_AUDITED = ("job_error",)


def upgrade() -> None:
    op.execute("create schema procrastinate")
    op.execute("set local search_path = procrastinate, public")
    op.execute(SCHEMA_SQL.read_text())
    op.execute("set local search_path = public")
    op.execute("""
        grant usage on schema procrastinate to confluo_app;
        grant select, insert, update, delete on all tables in schema procrastinate to confluo_app;
        grant usage, select on all sequences in schema procrastinate to confluo_app;
        grant execute on all functions in schema procrastinate to confluo_app;
        alter role confluo_app set search_path = "$user", public, procrastinate, extensions;
    """)

    op.execute("""
        create function app.record_inbound_event(
          p_provider text, p_external_id text, p_payload jsonb, p_headers jsonb
        ) returns uuid
        language sql security definer set search_path = public, pg_temp as $$
          insert into inbound_event (provider, external_id, payload, headers)
          values (p_provider, p_external_id, p_payload, p_headers)
          on conflict (provider, external_id) do nothing
          returning id
        $$;

        create function app.start_inbound_event(p_id uuid) returns inbound_event
        language sql security definer set search_path = public, pg_temp as $$
          update inbound_event set status = 'processing', attempts = attempts + 1
          where id = p_id
          returning *
        $$;

        create function app.finish_inbound_event(
          p_id uuid, p_status text, p_tenant uuid, p_error text
        ) returns void
        language sql security definer set search_path = public, pg_temp as $$
          update inbound_event
          set status = p_status,
              tenant_id = coalesce(p_tenant, tenant_id),
              last_error = p_error,
              processed_at = case when p_status = 'processed' then now() end
          where id = p_id
        $$;

        create table job_error (
          job_id bigint primary key,
          tenant_id uuid not null default app.current_tenant_id()
            references tenant(id) on delete cascade,
          task_name text not null,
          attempts int not null,
          error text not null,
          failed_at timestamptz not null default now()
        );
        create index job_error_tenant_idx on job_error (tenant_id, failed_at desc);
        alter table job_error enable row level security;
        create policy job_error_tenant on job_error for all
          using (tenant_id = app.current_tenant_id())
          with check (tenant_id = app.current_tenant_id());
        grant select, insert, update on job_error to confluo_app;

        create function app.audit_event(
          p_action text, p_entity text, p_entity_id text, p_diff jsonb
        ) returns void
        language plpgsql security definer set search_path = public, pg_temp as $$
        begin
          if app.current_tenant_id() is null then
            raise exception 'app.audit_event needs a tenant' using errcode = '42501';
          end if;
          insert into audit_log (tenant_id, actor_type, actor_id, action, entity, entity_id, diff, ip)
          values (
            app.current_tenant_id(),
            coalesce(nullif(current_setting('app.actor_type', true), ''),
                     case when app.current_user_id() is null then 'system' else 'user' end),
            app.current_user_id(), p_action, p_entity, p_entity_id, coalesce(p_diff, '{}'::jsonb),
            nullif(current_setting('app.client_ip', true), '')::inet
          );
        end $$;
        revoke all on function app.audit_event(text, text, text, jsonb) from public;
        grant execute on function app.audit_event(text, text, text, jsonb) to confluo_app;

        revoke all on function app.record_inbound_event(text, text, jsonb, jsonb) from public;
        revoke all on function app.start_inbound_event(uuid) from public;
        revoke all on function app.finish_inbound_event(uuid, text, uuid, text) from public;
        grant execute on function app.record_inbound_event(text, text, jsonb, jsonb) to confluo_app;
        grant execute on function app.start_inbound_event(uuid) to confluo_app;
        grant execute on function app.finish_inbound_event(uuid, text, uuid, text) to confluo_app;
    """)


def downgrade() -> None:
    op.execute("""
        drop function if exists app.finish_inbound_event(uuid, text, uuid, text);
        drop function if exists app.start_inbound_event(uuid);
        drop function if exists app.record_inbound_event(text, text, jsonb, jsonb);
        drop table if exists job_error;
        drop function if exists app.audit_event(text, text, text, jsonb);
        alter role confluo_app reset search_path;
        drop schema if exists procrastinate cascade;
    """)
