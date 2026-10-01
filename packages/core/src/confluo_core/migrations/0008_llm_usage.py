"""Per-tenant LLM and embedding usage (tokens, latency, estimated cost).

Revision ID: 0008_llm_usage
Revises: 0007_job_queue
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_llm_usage"
down_revision: str | Sequence[str] | None = "0007_job_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOT_AUDITED = ("llm_usage",)  # a metering log


def upgrade() -> None:
    op.execute("""
        create table llm_usage (
          id bigint generated always as identity primary key,
          tenant_id uuid not null default app.current_tenant_id()
            references tenant(id) on delete cascade,
          purpose text not null,
          tier text not null,
          provider text not null,
          model text not null,
          input_tokens int not null default 0,
          output_tokens int not null default 0,
          cache_read_tokens int not null default 0,
          cache_write_tokens int not null default 0,
          latency_ms int,
          cost_usd numeric(12, 6),
          run_id uuid,
          created_at timestamptz not null default now()
        );
        create index llm_usage_tenant_idx on llm_usage (tenant_id, created_at desc);

        alter table llm_usage enable row level security;
        create policy llm_usage_insert on llm_usage for insert
          with check (tenant_id = app.current_tenant_id());
        create policy llm_usage_select on llm_usage for select
          using (tenant_id = app.current_tenant_id() and app.is_manager());
        grant select, insert on llm_usage to confluo_app;
    """)


def downgrade() -> None:
    op.execute("drop table if exists llm_usage")
