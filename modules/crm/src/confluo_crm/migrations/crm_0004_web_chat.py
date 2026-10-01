"""Web chat: find a channel connection by its public key before the tenant is known.

The widget only knows its public key (crm_channel_connection.external_account_id for
channel 'web'). RLS hides connections without a tenant context, so this SECURITY
DEFINER function returns just what the public endpoints need.

Revision ID: crm_0004
Revises: crm_0003
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "crm_0004"
down_revision: str | Sequence[str] | None = "crm_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        create function app.crm_connection_by_key(p_channel text, p_key text)
        returns table (tenant_id uuid, connection_id uuid, status text, settings jsonb)
        language sql stable security definer set search_path = public, pg_temp as $$
          select c.tenant_id, c.id, c.status, c.settings
          from crm_channel_connection c
          where c.channel = p_channel and c.external_account_id = p_key
        $$;
        revoke all on function app.crm_connection_by_key(text, text) from public;
        grant execute on function app.crm_connection_by_key(text, text) to confluo_app;
    """)


def downgrade() -> None:
    op.execute("drop function if exists app.crm_connection_by_key(text, text)")
