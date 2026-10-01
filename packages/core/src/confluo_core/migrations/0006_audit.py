"""Audit trail: a trigger on every tenant table, append-only logs.

- `app.audit()` runs AFTER INSERT/UPDATE/DELETE on every audited table and writes one
  audit_log row: actor from `app.actor_type` / `app.user_id`, client IP from
  `app.client_ip` (set per transaction by confluo_core.tenancy), and the changed
  columns. It is SECURITY DEFINER, so it logs even where the caller couldn't insert.
- The app role can no longer insert into audit_log itself: only the trigger writes,
  so rows can't be forged.
- audit_log rejects UPDATE, DELETE and TRUNCATE for every role (owner included),
  except the cascade when a whole tenant is deleted.
- ai_action may only change its approval columns after insert.

Tables listed in NOT_AUDITED are logs or derived data.

Revision ID: 0006_audit
Revises: 0005_owner_rule_tenant_delete
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_audit"
down_revision: str | Sequence[str] | None = "0005_owner_rule_tenant_delete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = "crm_0001"

# Logs, queues and derived rows; everything else in public is audited.
NOT_AUDITED = (
    "alembic_version",
    "audit_log",
    "ai_action",
    "inbound_event",
    "outbox_event",
    "notification",
    "app_user",  # global, not tenant-scoped
    "crm_knowledge_chunk",  # derived from crm_knowledge_item
    "crm_external_busy",  # cache of external calendars
)


def upgrade() -> None:
    op.execute("""
        create function app.audit() returns trigger
        language plpgsql security definer set search_path = public, pg_temp as $$
        declare
          v_old jsonb := case when tg_op <> 'INSERT' then to_jsonb(old) end;
          v_new jsonb := case when tg_op <> 'DELETE' then to_jsonb(new) end;
          v_row jsonb := coalesce(v_new, v_old);
          v_noise text[] := array['created_at', 'updated_at', 'embedding', 'tsv'];
          v_diff jsonb;
          v_tenant uuid;
          v_key text;
          v_actor text := nullif(current_setting('app.actor_type', true), '');
        begin
          v_tenant := case when tg_table_name = 'tenant'
                           then (v_row->>'id')::uuid else (v_row->>'tenant_id')::uuid end;
          -- Cascaded deletes of a tenant's rows: the tenant is gone, nothing to log into.
          if not exists (select from tenant where id = v_tenant) then
            return null;
          end if;

          if tg_op = 'UPDATE' then
            v_diff := jsonb_build_object('old', '{}'::jsonb, 'new', '{}'::jsonb);
            for v_key in select jsonb_object_keys(v_new) loop
              if v_key <> all (v_noise) and v_new->v_key is distinct from v_old->v_key then
                v_diff := jsonb_set(v_diff, array['old', v_key], coalesce(v_old->v_key, 'null'));
                v_diff := jsonb_set(v_diff, array['new', v_key], coalesce(v_new->v_key, 'null'));
              end if;
            end loop;
            if v_diff->'new' = '{}'::jsonb then
              return null;  -- only timestamps changed
            end if;
          elsif tg_op = 'INSERT' then
            v_diff := jsonb_build_object('new', v_new - v_noise);
          else
            v_diff := jsonb_build_object('old', v_old - v_noise);
          end if;

          insert into audit_log (tenant_id, actor_type, actor_id, action, entity, entity_id, diff, ip)
          values (
            v_tenant,
            coalesce(v_actor, case when app.current_user_id() is null then 'system' else 'user' end),
            app.current_user_id(),
            lower(tg_op),
            tg_table_name,
            coalesce(v_row->>'id', v_row->>'user_id', v_row->>'module_key', v_row->>'service_id'),
            v_diff,
            nullif(current_setting('app.client_ip', true), '')::inet
          );
          return null;
        end $$;
    """)

    exclude = ", ".join(f"'{t}'" for t in NOT_AUDITED)
    op.execute(f"""
        do $$
        declare t text;
        begin
          for t in
            select tablename from pg_tables
            where schemaname = 'public' and tablename not in ({exclude})
          loop
            execute format(
              'create trigger %I after insert or update or delete on %I
                 for each row execute function app.audit()', t || '_audit', t);
          end loop;
        end $$;
    """)

    # Only the trigger writes the audit log; members read it if they're managers.
    op.execute("""
        revoke insert on audit_log from confluo_app;
        drop policy audit_log_tenant on audit_log;
        create policy audit_log_select on audit_log for select
          using (tenant_id = app.current_tenant_id() and app.is_manager());

        create function app.audit_log_append_only() returns trigger
        language plpgsql as $$
        begin
          if tg_op = 'DELETE' and not exists (select from tenant where id = old.tenant_id) then
            return old;  -- the tenant itself is being deleted
          end if;
          raise exception 'audit_log is append-only' using errcode = '42501';
        end $$;

        create trigger audit_log_no_update before update or delete on audit_log
          for each row execute function app.audit_log_append_only();
        create trigger audit_log_no_truncate before truncate on audit_log
          for each statement execute function app.audit_log_append_only();
    """)

    # ai_action: written once by the node decorator; later only the approval may change.
    op.execute("""
        create function app.ai_action_guard() returns trigger
        language plpgsql as $$
        begin
          if tg_op = 'DELETE' then
            if not exists (select from tenant where id = old.tenant_id) then
              return old;
            end if;
            raise exception 'ai_action is append-only' using errcode = '42501';
          end if;
          if (to_jsonb(new) - array['approval_status', 'approved_by'])
             is distinct from (to_jsonb(old) - array['approval_status', 'approved_by']) then
            raise exception 'only the approval of an ai_action can change' using errcode = '42501';
          end if;
          return new;
        end $$;

        create trigger ai_action_guard before update or delete on ai_action
          for each row execute function app.ai_action_guard();
        create trigger ai_action_no_truncate before truncate on ai_action
          for each statement execute function app.audit_log_append_only();
    """)


def downgrade() -> None:
    exclude = ", ".join(f"'{t}'" for t in NOT_AUDITED)
    op.execute(f"""
        drop trigger if exists ai_action_no_truncate on ai_action;
        drop trigger if exists ai_action_guard on ai_action;
        drop function if exists app.ai_action_guard();
        drop trigger if exists audit_log_no_truncate on audit_log;
        drop trigger if exists audit_log_no_update on audit_log;
        drop function if exists app.audit_log_append_only();
        drop policy if exists audit_log_select on audit_log;
        create policy audit_log_tenant on audit_log for all
          using (tenant_id = app.current_tenant_id())
          with check (tenant_id = app.current_tenant_id());
        grant insert on audit_log to confluo_app;
        do $$
        declare t text;
        begin
          for t in
            select tablename from pg_tables
            where schemaname = 'public' and tablename not in ({exclude})
          loop
            execute format('drop trigger if exists %I on %I', t || '_audit', t);
          end loop;
        end $$;
        drop function if exists app.audit();
    """)
