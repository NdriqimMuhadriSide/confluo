"""Tenancy: tenant, app_user, tenant_member, location, with row-level security.

The API and worker connect as `confluo_app`, which owns nothing and cannot bypass
RLS. Per transaction they set two settings (see confluo_core.tenancy):

- `app.user_id`   the signed-in staff user (empty for system jobs)
- `app.tenant_id` the tenant the request acts for, set only after a membership check

Without `app.tenant_id` every tenant-scoped policy evaluates to false, so a
missing context fails closed (no rows, inserts rejected).

Revision ID: 0001_tenancy
Revises:
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_tenancy"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("core",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Roles are cluster-wide, so create it only once (dev and test databases share it).
    # It starts without LOGIN; `make migrate` sets a password per environment.
    op.execute("""
        do $$ begin
          if not exists (select from pg_roles where rolname = 'confluo_app') then
            create role confluo_app nologin nobypassrls;
          end if;
        end $$;
    """)
    op.execute("grant usage on schema public to confluo_app")

    op.execute("create schema app")
    op.execute("grant usage on schema app to confluo_app")
    op.execute("""
        create function app.current_tenant_id() returns uuid
        language sql stable as $$
          select nullif(current_setting('app.tenant_id', true), '')::uuid
        $$;

        create function app.current_user_id() returns uuid
        language sql stable as $$
          select nullif(current_setting('app.user_id', true), '')::uuid
        $$;

        create function app.set_updated_at() returns trigger
        language plpgsql as $$
        begin
          new.updated_at := now();
          return new;
        end $$;
    """)

    op.execute("""
        create table tenant (
          id uuid primary key default gen_random_uuid(),
          name text not null check (length(trim(name)) between 1 and 120),
          slug text not null unique check (slug ~ '^[a-z0-9](-?[a-z0-9])+$' and length(slug) <= 40),
          default_locale text not null default 'en',
          timezone text not null default 'Europe/Brussels',
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now()
        );

        -- One row per Supabase Auth user (id = auth user id). Global, not tenant-scoped:
        -- one person can belong to several tenants.
        create table app_user (
          id uuid primary key,
          email text not null,
          name text,
          locale text,
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now()
        );

        create table tenant_member (
          tenant_id uuid not null references tenant(id) on delete cascade,
          user_id uuid not null references app_user(id) on delete cascade,
          role text not null default 'staff' check (role in ('owner', 'admin', 'staff')),
          status text not null default 'active' check (status in ('active', 'invited', 'disabled')),
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now(),
          primary key (tenant_id, user_id)
        );
        create index tenant_member_user_idx on tenant_member (user_id);

        create table location (
          id uuid primary key default gen_random_uuid(),
          tenant_id uuid not null default app.current_tenant_id()
            references tenant(id) on delete cascade,
          name text not null check (length(trim(name)) between 1 and 120),
          address text,
          timezone text,
          opening_hours jsonb not null default '{}'::jsonb,
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now()
        );
        create index location_tenant_idx on location (tenant_id);
    """)
    for table in ("tenant", "app_user", "tenant_member", "location"):
        op.execute(f"""
            create trigger {table}_updated_at before update on {table}
            for each row execute function app.set_updated_at()
        """)

    # --- Row-level security -----------------------------------------------------
    op.execute("""
        alter table tenant enable row level security;
        alter table app_user enable row level security;
        alter table tenant_member enable row level security;
        alter table location enable row level security;

        -- A user sees the tenants they are an active member of.
        create policy tenant_select on tenant for select using (
          id = app.current_tenant_id()
          or id in (select tenant_id from tenant_member
                    where user_id = app.current_user_id() and status = 'active')
        );
        create policy tenant_update on tenant for update
          using (id = app.current_tenant_id()) with check (id = app.current_tenant_id());

        -- A user sees and maintains their own row, and sees fellow members of the
        -- current tenant.
        create policy app_user_select on app_user for select using (
          id = app.current_user_id()
          or id in (select user_id from tenant_member where tenant_id = app.current_tenant_id())
        );
        create policy app_user_insert on app_user for insert
          with check (id = app.current_user_id());
        create policy app_user_update on app_user for update
          using (id = app.current_user_id()) with check (id = app.current_user_id());

        -- Members of the current tenant, plus the user's own memberships (needed for
        -- the membership check and the tenant list before a tenant is chosen).
        -- Role checks on who may change members come with the RBAC card.
        create policy tenant_member_select on tenant_member for select using (
          tenant_id = app.current_tenant_id() or user_id = app.current_user_id()
        );
        create policy tenant_member_write on tenant_member for all
          using (tenant_id = app.current_tenant_id())
          with check (tenant_id = app.current_tenant_id());

        -- The pattern for every ordinary tenant-scoped table.
        create policy location_tenant on location for all
          using (tenant_id = app.current_tenant_id())
          with check (tenant_id = app.current_tenant_id());
    """)

    op.execute("""
        grant select, update on tenant to confluo_app;
        grant select, insert, update on app_user to confluo_app;
        grant select, insert, update, delete on tenant_member to confluo_app;
        grant select, insert, update, delete on location to confluo_app;
    """)

    # Creating a tenant needs rights the app role doesn't have (no INSERT on tenant,
    # and no tenant context yet). This function runs as the owner and makes the
    # calling user (app.user_id) the owner of the new tenant.
    op.execute("""
        create function app.create_tenant(p_name text, p_slug text) returns uuid
        language plpgsql security definer set search_path = public, pg_temp as $$
        declare
          v_user uuid := app.current_user_id();
          v_tenant uuid;
        begin
          if v_user is null then
            raise exception 'app.user_id is not set' using errcode = '42501';
          end if;
          if not exists (select from app_user where id = v_user) then
            raise exception 'app_user % does not exist', v_user using errcode = '23503';
          end if;
          insert into tenant (name, slug) values (p_name, p_slug) returning id into v_tenant;
          insert into tenant_member (tenant_id, user_id, role) values (v_tenant, v_user, 'owner');
          return v_tenant;
        end $$;

        revoke all on function app.create_tenant(text, text) from public;
        grant execute on function app.create_tenant(text, text) to confluo_app;
    """)


def downgrade() -> None:
    # The role is cluster-wide and may serve other databases, so it is not dropped.
    op.execute("""
        drop table if exists location, tenant_member, app_user, tenant cascade;
        drop schema if exists app cascade;
        revoke usage on schema public from confluo_app;
    """)
