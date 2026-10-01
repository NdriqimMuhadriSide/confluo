"""Per-tenant module enablement and config.

A missing row means "module default" (ConfluoModule.enabled_by_default and the
defaults of its config_schema), so new modules need no backfill.

Revision ID: 0003_tenant_module
Revises: 0002_rbac
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_tenant_module"
down_revision: str | Sequence[str] | None = "0002_rbac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        create table tenant_module (
          tenant_id uuid not null default app.current_tenant_id()
            references tenant(id) on delete cascade,
          module_key text not null check (module_key ~ '^[a-z][a-z0-9_]*$'),
          enabled boolean not null,
          config jsonb not null default '{}'::jsonb check (jsonb_typeof(config) = 'object'),
          version text,
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now(),
          primary key (tenant_id, module_key)
        );
        create trigger tenant_module_updated_at before update on tenant_module
          for each row execute function app.set_updated_at();

        alter table tenant_module enable row level security;
        create policy tenant_module_select on tenant_module for select
          using (tenant_id = app.current_tenant_id());
        create policy tenant_module_write on tenant_module for all
          using (tenant_id = app.current_tenant_id() and app.is_manager())
          with check (tenant_id = app.current_tenant_id() and app.is_manager());

        grant select, insert, update on tenant_module to confluo_app;
    """)


def downgrade() -> None:
    op.execute("drop table if exists tenant_module")
