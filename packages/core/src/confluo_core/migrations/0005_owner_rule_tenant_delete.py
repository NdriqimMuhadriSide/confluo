"""Let a tenant be deleted: the keep-an-owner trigger blocked the cascaded member
deletes. During `delete from tenant` the tenant row is already gone when the cascade
removes its members, so the rule now only applies while the tenant still exists.

Revision ID: 0005_owner_rule_tenant_delete
Revises: 0004_core_data_model
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_owner_rule_tenant_delete"
down_revision: str | Sequence[str] | None = "0004_core_data_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEEP_AN_OWNER = """
create or replace function app.keep_an_owner() returns trigger
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  if old.role = 'owner' and old.status = 'active'
     and (tg_op = 'DELETE' or new.role <> 'owner' or new.status <> 'active')
     {tenant_check}
     and not exists (
       select from tenant_member
       where tenant_id = old.tenant_id and role = 'owner' and status = 'active'
         and user_id <> old.user_id
     ) then
    raise exception 'a tenant must keep at least one owner' using errcode = '23514';
  end if;
  return coalesce(new, old);
end $$;
"""


def upgrade() -> None:
    op.execute(
        KEEP_AN_OWNER.format(
            tenant_check="and exists (select from tenant where id = old.tenant_id)"
        )
    )


def downgrade() -> None:
    op.execute(KEEP_AN_OWNER.format(tenant_check=""))
