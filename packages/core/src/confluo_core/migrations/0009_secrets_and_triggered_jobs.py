"""Encrypted secrets, and enqueueing jobs from database triggers.

- `secret`: per-tenant encrypted blobs (OAuth refresh tokens, provider keys). Rows
  hold AES-GCM ciphertext only; the key lives in the app environment
  (CONFLUO_SECRETS_KEY), so a database dump alone reveals nothing. Not audited: the
  audit diff would copy the ciphertext around for no benefit.
- `app.defer_job(task, args, lock)`: enqueue a Procrastinate job from SQL, in the
  caller's transaction. Lets a trigger react to a write no matter which code path
  made it (e.g. pushing appointment changes to external calendars).

Revision ID: 0009_secrets_jobs
Revises: 0008_llm_usage
Create Date: 2026-10-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_secrets_jobs"
down_revision: str | Sequence[str] | None = "0008_llm_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOT_AUDITED = ("secret",)


def upgrade() -> None:
    op.execute("""
        create table secret (
          id uuid primary key default gen_random_uuid(),
          tenant_id uuid not null default app.current_tenant_id()
            references tenant(id) on delete cascade,
          purpose text not null,
          ciphertext bytea not null,
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now()
        );
        create trigger secret_updated_at before update on secret
          for each row execute function app.set_updated_at();
        alter table secret enable row level security;
        create policy secret_tenant on secret for all
          using (tenant_id = app.current_tenant_id())
          with check (tenant_id = app.current_tenant_id());
        grant select, insert, update, delete on secret to confluo_app;

        create function app.defer_job(p_task text, p_args jsonb, p_lock text default null)
        returns bigint
        -- Procrastinate's functions name their tables unqualified.
        language sql set search_path = procrastinate, public, pg_temp as $$
          select (procrastinate.procrastinate_defer_jobs_v1(array[
            row('default', p_task, 0, p_lock, null, p_args, null)::procrastinate.procrastinate_job_to_defer_v1
          ]))[1]
        $$;
        grant execute on function app.defer_job(text, jsonb, text) to confluo_app;
    """)


def downgrade() -> None:
    op.execute("""
        drop function if exists app.defer_job(text, jsonb, text);
        drop table if exists secret;
    """)
