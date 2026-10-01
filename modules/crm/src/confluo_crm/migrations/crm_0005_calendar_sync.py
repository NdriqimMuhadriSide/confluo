"""External calendar sync (Microsoft 365 first, Google next).

- crm_calendar_connection: one per resource, with account, status, delta link
  (sync_token), last sync/error and the push-notification subscription.
- A trigger on crm_appointment enqueues `crm:push_appointment` whenever a booking's
  time, status or resource changes (or it is deleted) and an external calendar is
  connected, whatever code path made the change. The job writes back
  external_event_id, which doesn't re-trigger (the trigger only watches the
  columns that matter).
- app.crm_calendar_by_subscription: routes a provider notification (which arrives
  before any tenant is known) to its connection.
- app.crm_defer_calendar_syncs: the periodic job's fan-out over every tenant's active
  connections (skipping those with a sync already waiting).

Revision ID: crm_0005
Revises: crm_0004
Create Date: 2026-10-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "crm_0005"
down_revision: str | Sequence[str] | None = "crm_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = "0009_secrets_jobs"


def upgrade() -> None:
    op.execute("""
        alter table crm_calendar_connection
          add column account_email text,
          add column status text not null default 'active'
            check (status in ('active', 'error', 'revoked')),
          add column last_synced_at timestamptz,
          add column last_error text,
          add column window_end timestamptz,
          add column subscription_id text unique,
          add column subscription_expires_at timestamptz,
          add column client_state text,
          add constraint crm_calendar_connection_resource_key unique (resource_id);

        create function app.crm_push_appointment() returns trigger
        language plpgsql as $$
        declare
          v_row crm_appointment := coalesce(new, old);
        begin
          if exists (
            select from crm_calendar_connection
            where resource_id in (v_row.resource_id, old.resource_id) and status = 'active'
          ) then
            perform app.defer_job(
              'crm:push_appointment',
              jsonb_build_object(
                'tenant_id', v_row.tenant_id,
                'appointment_id', v_row.id,
                'deleted', tg_op = 'DELETE',
                -- When the booking moves to another resource (or is deleted), the old
                -- calendar's event must go too.
                'old_resource_id', case when tg_op <> 'INSERT' then old.resource_id end,
                'old_event_id', case when tg_op <> 'INSERT' then old.external_event_id end
              ),
              'cal:' || v_row.id
            );
          end if;
          return null;
        end $$;

        create trigger crm_appointment_calendar_push
          after insert or delete or update of during, status, resource_id on crm_appointment
          for each row execute function app.crm_push_appointment();

        create function app.crm_calendar_by_subscription(p_subscription text)
        returns table (tenant_id uuid, connection_id uuid, client_state text)
        language sql stable security definer set search_path = public, pg_temp as $$
          select tenant_id, id, client_state from crm_calendar_connection
          where subscription_id = p_subscription
        $$;
        revoke all on function app.crm_calendar_by_subscription(text) from public;
        grant execute on function app.crm_calendar_by_subscription(text) to confluo_app;

        create function app.crm_defer_calendar_syncs() returns integer
        language sql security definer set search_path = public, pg_temp as $$
          select count(app.defer_job(
                   'crm:sync_calendar',
                   jsonb_build_object('tenant_id', c.tenant_id, 'connection_id', c.id),
                   'calsync:' || c.id))::integer
          from crm_calendar_connection c
          where c.status = 'active'
            and not exists (
              select from procrastinate.procrastinate_jobs j
              where j.lock = 'calsync:' || c.id and j.status = 'todo')
        $$;
        revoke all on function app.crm_defer_calendar_syncs() from public;
        grant execute on function app.crm_defer_calendar_syncs() to confluo_app;
    """)


def downgrade() -> None:
    op.execute("""
        drop function if exists app.crm_defer_calendar_syncs();
        drop function if exists app.crm_calendar_by_subscription(text);
        drop trigger if exists crm_appointment_calendar_push on crm_appointment;
        drop function if exists app.crm_push_appointment();
        alter table crm_calendar_connection
          drop constraint if exists crm_calendar_connection_resource_key,
          drop column if exists account_email, drop column if exists status,
          drop column if exists last_synced_at, drop column if exists last_error,
          drop column if exists window_end, drop column if exists subscription_id,
          drop column if exists subscription_expires_at, drop column if exists client_state;
    """)
