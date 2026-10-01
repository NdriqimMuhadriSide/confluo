"""RBAC: role-aware member policies and email invitations.

Permission checks live in the API (confluo_core.permissions). The rules that stop
privilege escalation are repeated here so a bug in an endpoint can't bypass them:

- only owners and admins can change or remove members, or manage invitations
- only an owner can grant the owner role or touch an owner's membership
- a tenant always keeps at least one active owner
- members are only added through app.create_tenant() and app.accept_invitation()
- inside a tenant context only that tenant is visible: a user's memberships in other
  tenants (and those tenants) are visible only while no tenant is set, which is when
  /api/me lists them and the membership check runs

Accepting an invitation matches `app.user_email`, which the API sets from the
verified Supabase token.

Revision ID: 0002_rbac
Revises: 0001_tenancy
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_rbac"
down_revision: str | Sequence[str] | None = "0001_tenancy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        create function app.current_user_email() returns text
        language sql stable as $$
          select nullif(lower(current_setting('app.user_email', true)), '')
        $$;

        -- The caller's role in the current tenant. SECURITY DEFINER so policies on
        -- tenant_member can use it without recursing into their own RLS.
        create function app.current_member_role() returns text
        language sql stable security definer set search_path = public, pg_temp as $$
          select role from tenant_member
          where tenant_id = app.current_tenant_id()
            and user_id = app.current_user_id()
            and status = 'active'
        $$;
        grant execute on function app.current_member_role() to confluo_app;

        create function app.is_manager() returns boolean
        language sql stable as $$
          select coalesce(app.current_member_role() in ('owner', 'admin'), false)
        $$;
    """)

    # --- Members ------------------------------------------------------------------
    op.execute("""
        drop policy tenant_member_select on tenant_member;
        create policy tenant_member_select on tenant_member for select using (
          tenant_id = app.current_tenant_id()
          or (app.current_tenant_id() is null and user_id = app.current_user_id())
        );

        drop policy tenant_select on tenant;
        create policy tenant_select on tenant for select using (
          id = app.current_tenant_id()
          or (app.current_tenant_id() is null
              and id in (select tenant_id from tenant_member
                         where user_id = app.current_user_id() and status = 'active'))
        );

        drop policy tenant_member_write on tenant_member;

        create policy tenant_member_update on tenant_member for update
          using (
            tenant_id = app.current_tenant_id() and app.is_manager()
            and (role <> 'owner' or app.current_member_role() = 'owner')
          )
          with check (
            tenant_id = app.current_tenant_id() and app.is_manager()
            and (role <> 'owner' or app.current_member_role() = 'owner')
          );

        create policy tenant_member_delete on tenant_member for delete
          using (
            tenant_id = app.current_tenant_id() and app.is_manager()
            and (role <> 'owner' or app.current_member_role() = 'owner')
          );

        revoke insert on tenant_member from confluo_app;

        -- Keep at least one active owner per tenant.
        create function app.keep_an_owner() returns trigger
        language plpgsql security definer set search_path = public, pg_temp as $$
        begin
          if old.role = 'owner' and old.status = 'active'
             and (tg_op = 'DELETE' or new.role <> 'owner' or new.status <> 'active')
             and not exists (
               select from tenant_member
               where tenant_id = old.tenant_id and role = 'owner' and status = 'active'
                 and user_id <> old.user_id
             ) then
            raise exception 'a tenant must keep at least one owner' using errcode = '23514';
          end if;
          return coalesce(new, old);
        end $$;

        create trigger tenant_member_keep_owner
          before update or delete on tenant_member
          for each row execute function app.keep_an_owner();
    """)

    # Only managers may edit the tenant itself.
    op.execute("""
        drop policy tenant_update on tenant;
        create policy tenant_update on tenant for update
          using (id = app.current_tenant_id() and app.is_manager())
          with check (id = app.current_tenant_id() and app.is_manager());
    """)

    # --- Invitations ----------------------------------------------------------------
    op.execute("""
        create table tenant_invitation (
          id uuid primary key default gen_random_uuid(),
          tenant_id uuid not null default app.current_tenant_id()
            references tenant(id) on delete cascade,
          email text not null check (email = lower(email) and email like '%_@_%'),
          role text not null default 'staff' check (role in ('owner', 'admin', 'staff')),
          invited_by uuid default app.current_user_id() references app_user(id) on delete set null,
          created_at timestamptz not null default now(),
          expires_at timestamptz not null default now() + interval '14 days',
          accepted_at timestamptz,
          accepted_by uuid references app_user(id) on delete set null,
          revoked_at timestamptz,
          updated_at timestamptz not null default now()
        );
        -- One open invitation per email per tenant.
        create unique index tenant_invitation_open_idx on tenant_invitation (tenant_id, email)
          where accepted_at is null and revoked_at is null;
        create index tenant_invitation_email_idx on tenant_invitation (email)
          where accepted_at is null and revoked_at is null;

        create trigger tenant_invitation_updated_at before update on tenant_invitation
          for each row execute function app.set_updated_at();

        alter table tenant_invitation enable row level security;

        create policy tenant_invitation_select on tenant_invitation for select
          using (tenant_id = app.current_tenant_id() and app.is_manager());
        create policy tenant_invitation_insert on tenant_invitation for insert
          with check (
            tenant_id = app.current_tenant_id() and app.is_manager()
            and (role <> 'owner' or app.current_member_role() = 'owner')
          );
        create policy tenant_invitation_update on tenant_invitation for update
          using (tenant_id = app.current_tenant_id() and app.is_manager())
          with check (
            tenant_id = app.current_tenant_id() and app.is_manager()
            and (role <> 'owner' or app.current_member_role() = 'owner')
          );

        grant select, insert, update on tenant_invitation to confluo_app;
    """)

    # The invitee isn't a member yet, so they reach their invitations only through
    # these functions, matched on the verified email from their token.
    op.execute("""
        create function app.my_invitations()
        returns table (id uuid, tenant_id uuid, tenant_name text, role text, expires_at timestamptz)
        language sql stable security definer set search_path = public, pg_temp as $$
          select i.id, i.tenant_id, t.name, i.role, i.expires_at
          from tenant_invitation i join tenant t on t.id = i.tenant_id
          where i.email = app.current_user_email()
            and i.accepted_at is null and i.revoked_at is null and i.expires_at > now()
            and not exists (
              select from tenant_member m
              where m.tenant_id = i.tenant_id and m.user_id = app.current_user_id()
                and m.status = 'active'
            )
          order by t.name
        $$;

        create function app.accept_invitation(p_invitation uuid) returns uuid
        language plpgsql security definer set search_path = public, pg_temp as $$
        declare
          v_user uuid := app.current_user_id();
          v_email text := app.current_user_email();
          v_inv tenant_invitation;
        begin
          if v_user is null or v_email is null then
            raise exception 'app.user_id and app.user_email must be set' using errcode = '42501';
          end if;
          select * into v_inv from tenant_invitation
          where id = p_invitation and email = v_email
            and accepted_at is null and revoked_at is null and expires_at > now()
          for update;
          if not found then
            raise exception 'invitation not found' using errcode = 'P0002';
          end if;

          insert into app_user (id, email) values (v_user, v_email)
            on conflict (id) do nothing;
          insert into tenant_member (tenant_id, user_id, role, status)
            values (v_inv.tenant_id, v_user, v_inv.role, 'active')
            on conflict (tenant_id, user_id) do update
              set role = excluded.role, status = 'active';
          update tenant_invitation set accepted_at = now(), accepted_by = v_user
            where id = v_inv.id;
          return v_inv.tenant_id;
        end $$;

        revoke all on function app.my_invitations() from public;
        revoke all on function app.accept_invitation(uuid) from public;
        grant execute on function app.my_invitations() to confluo_app;
        grant execute on function app.accept_invitation(uuid) to confluo_app;
    """)


def downgrade() -> None:
    op.execute("""
        drop function if exists app.accept_invitation(uuid);
        drop function if exists app.my_invitations();
        drop table if exists tenant_invitation;
        drop trigger if exists tenant_member_keep_owner on tenant_member;
        drop function if exists app.keep_an_owner();
        drop policy if exists tenant_member_update on tenant_member;
        drop policy if exists tenant_member_delete on tenant_member;
        create policy tenant_member_write on tenant_member for all
          using (tenant_id = app.current_tenant_id())
          with check (tenant_id = app.current_tenant_id());
        grant insert on tenant_member to confluo_app;
        drop policy if exists tenant_update on tenant;
        create policy tenant_update on tenant for update
          using (id = app.current_tenant_id()) with check (id = app.current_tenant_id());
        drop policy if exists tenant_member_select on tenant_member;
        create policy tenant_member_select on tenant_member for select using (
          tenant_id = app.current_tenant_id() or user_id = app.current_user_id()
        );
        drop policy if exists tenant_select on tenant;
        create policy tenant_select on tenant for select using (
          id = app.current_tenant_id()
          or id in (select tenant_id from tenant_member
                    where user_id = app.current_user_id() and status = 'active')
        );
        drop function if exists app.is_manager();
        drop function if exists app.current_member_role();
        drop function if exists app.current_user_email();
    """)
