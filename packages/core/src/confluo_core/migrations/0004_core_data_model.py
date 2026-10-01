"""Core data model (ARCHITECTURE.md §3, "Core").

Every tenant-scoped table gets `tenant_id ... default app.current_tenant_id()`, the
standard RLS policy and a `unique (tenant_id, id)` so other tables can reference it
with a composite foreign key. Composite keys make a cross-tenant reference
impossible even with a forged id: foreign-key checks ignore RLS, the composite key
doesn't.

Behaviour on top of these tables (audit writes, AI action logging, webhook
processing) comes with the cards that own it.

Revision ID: 0004_core_data_model
Revises: 0003_tenant_module
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_core_data_model"
down_revision: str | Sequence[str] | None = "0003_tenant_module"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_ID = "tenant_id uuid not null default app.current_tenant_id() references tenant(id) on delete cascade"
TIMESTAMPS = """
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
"""


def tenant_rls(table: str, grants: str = "select, insert, update, delete") -> None:
    """Standard isolation for a tenant-scoped table."""
    op.execute(f"""
        alter table {table} enable row level security;
        create policy {table}_tenant on {table} for all
          using (tenant_id = app.current_tenant_id())
          with check (tenant_id = app.current_tenant_id());
        grant {grants} on {table} to confluo_app;
    """)


def updated_at(table: str) -> None:
    op.execute(f"""
        create trigger {table}_updated_at before update on {table}
          for each row execute function app.set_updated_at()
    """)


def upgrade() -> None:
    # Extensions live in `extensions` (Supabase convention); CI's plain Postgres needs
    # the schema created. pgvector and btree_gist are used by the CRM branch.
    op.execute("""
        create schema if not exists extensions;
        grant usage on schema extensions to confluo_app;
        create extension if not exists vector with schema extensions;
        create extension if not exists btree_gist with schema extensions;
    """)

    # Lets other tables reference tenant-scoped rows by (tenant_id, id).
    op.execute(
        "alter table location add constraint location_tenant_id_id_key unique (tenant_id, id)"
    )

    # --- Custom fields ---------------------------------------------------------------
    op.execute(f"""
        create table field_definition (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          entity text not null check (entity in ('customer', 'appointment')),
          key text not null check (key ~ '^[a-z][a-z0-9_]{{0,62}}$'),
          label_i18n jsonb not null default '{{}}'::jsonb,
          type text not null check (type in
            ('text', 'long_text', 'number', 'date', 'boolean', 'select', 'phone', 'email')),
          options jsonb not null default '[]'::jsonb,
          validation jsonb not null default '{{}}'::jsonb,
          pii_level text not null default 'personal'
            check (pii_level in ('none', 'personal', 'sensitive')),
          position int not null default 0,
          archived_at timestamptz,
          {TIMESTAMPS},
          unique (tenant_id, id),
          unique (tenant_id, entity, key)
        );
    """)
    tenant_rls("field_definition")
    updated_at("field_definition")

    # --- Customers ---------------------------------------------------------------------
    op.execute(f"""
        create table customer (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          display_name text,
          first_name text,
          last_name text,
          preferred_language text,
          status text not null default 'active' check (status in ('active', 'merged', 'erased')),
          merged_into_id uuid,
          custom_fields jsonb not null default '{{}}'::jsonb,
          {TIMESTAMPS},
          unique (tenant_id, id),
          foreign key (tenant_id, merged_into_id) references customer (tenant_id, id),
          check ((status = 'merged') = (merged_into_id is not null))
        );
        create index customer_tenant_name_idx on customer (tenant_id, lower(display_name));

        create table customer_identity (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          customer_id uuid not null,
          type text not null check (type in
            ('phone', 'email', 'whatsapp', 'instagram', 'facebook', 'web_session')),
          value_normalized text not null,
          verified boolean not null default false,
          source_channel text,
          created_at timestamptz not null default now(),
          unique (tenant_id, id),
          foreign key (tenant_id, customer_id) references customer (tenant_id, id) on delete cascade
        );
        -- A verified identity belongs to exactly one customer per tenant.
        create unique index customer_identity_verified_idx
          on customer_identity (tenant_id, type, value_normalized) where verified;
        create index customer_identity_lookup_idx
          on customer_identity (tenant_id, type, value_normalized);

        create table consent (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          customer_id uuid not null,
          purpose text not null check (purpose in ('processing', 'marketing', 'recording')),
          granted boolean not null,
          channel text,
          text_version text,
          evidence jsonb not null default '{{}}'::jsonb,
          at timestamptz not null default now(),
          foreign key (tenant_id, customer_id) references customer (tenant_id, id) on delete cascade
        );
        create index consent_customer_idx on consent (tenant_id, customer_id, purpose, at desc);
    """)
    for t in ("customer", "customer_identity", "consent"):
        tenant_rls(t)
    updated_at("customer")

    # --- Audit and AI action logs (append-only; enforced by the audit card) ----------------
    op.execute(f"""
        create table audit_log (
          id bigint generated always as identity primary key,
          {TENANT_ID},
          actor_type text not null check (actor_type in ('user', 'ai', 'system')),
          actor_id uuid,
          action text not null,
          entity text not null,
          entity_id text,
          diff jsonb not null default '{{}}'::jsonb,
          ip inet,
          at timestamptz not null default now()
        );
        create index audit_log_entity_idx on audit_log (tenant_id, entity, entity_id, at desc);

        create table ai_action (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          run_id uuid not null,
          conversation_id uuid,
          node text not null,
          tool text,
          input jsonb not null default '{{}}'::jsonb,
          output jsonb not null default '{{}}'::jsonb,
          rationale text,
          sources jsonb not null default '[]'::jsonb,
          model text,
          input_tokens int,
          output_tokens int,
          latency_ms int,
          confidence real check (confidence between 0 and 1),
          outcome text,
          approval_status text not null default 'not_required'
            check (approval_status in ('not_required', 'pending', 'approved', 'rejected')),
          approved_by uuid references app_user(id) on delete set null,
          created_at timestamptz not null default now()
        );
        create index ai_action_run_idx on ai_action (tenant_id, run_id, created_at);
        create index ai_action_conversation_idx on ai_action (tenant_id, conversation_id, created_at);
    """)
    tenant_rls("audit_log", "select, insert")
    tenant_rls("ai_action", "select, insert, update")

    # --- Webhook ledger and outbox ------------------------------------------------------
    # inbound_event is written before the tenant is known (the adapter resolves it from
    # the channel connection), so tenant_id is nullable; the job-queue card adds the
    # system path that processes unassigned events.
    op.execute(f"""
        create table inbound_event (
          id uuid primary key default gen_random_uuid(),
          tenant_id uuid references tenant(id) on delete cascade,
          provider text not null,
          external_id text not null,
          payload jsonb not null,
          headers jsonb not null default '{{}}'::jsonb,
          status text not null default 'received'
            check (status in ('received', 'processing', 'processed', 'failed', 'dead')),
          attempts int not null default 0,
          last_error text,
          received_at timestamptz not null default now(),
          processed_at timestamptz,
          unique (provider, external_id)
        );
        alter table inbound_event enable row level security;
        create policy inbound_event_tenant on inbound_event for select
          using (tenant_id = app.current_tenant_id());

        create table outbox_event (
          id bigint generated always as identity primary key,
          {TENANT_ID},
          type text not null,
          payload jsonb not null default '{{}}'::jsonb,
          created_at timestamptz not null default now(),
          published_at timestamptz
        );
        create index outbox_event_unpublished_idx on outbox_event (id) where published_at is null;
    """)
    tenant_rls("outbox_event", "select, insert")

    # --- Notifications and GDPR requests ---------------------------------------------------
    op.execute(f"""
        create table notification (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          user_id uuid not null references app_user(id) on delete cascade,
          kind text not null,
          payload jsonb not null default '{{}}'::jsonb,
          read_at timestamptz,
          delivered_via text[] not null default '{{}}',
          created_at timestamptz not null default now()
        );
        create index notification_user_idx on notification (tenant_id, user_id, created_at desc);
        alter table notification enable row level security;
        -- Users see their own notifications; any tenant code may create them.
        create policy notification_own on notification for select
          using (tenant_id = app.current_tenant_id() and user_id = app.current_user_id());
        create policy notification_read on notification for update
          using (tenant_id = app.current_tenant_id() and user_id = app.current_user_id())
          with check (tenant_id = app.current_tenant_id() and user_id = app.current_user_id());
        create policy notification_insert on notification for insert
          with check (tenant_id = app.current_tenant_id());
        grant select, insert, update on notification to confluo_app;

        create table data_subject_request (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          customer_id uuid,
          type text not null check (type in ('export', 'erase')),
          status text not null default 'pending'
            check (status in ('pending', 'in_progress', 'completed', 'rejected')),
          requested_by uuid references app_user(id) on delete set null,
          completed_at timestamptz,
          artifact_url text,
          {TIMESTAMPS},
          foreign key (tenant_id, customer_id) references customer (tenant_id, id) on delete set null (customer_id)
        );
    """)
    tenant_rls("data_subject_request", "select, insert, update")
    updated_at("data_subject_request")


def downgrade() -> None:
    op.execute("""
        drop table if exists data_subject_request, notification, outbox_event, inbound_event,
          ai_action, audit_log, consent, customer_identity, customer, field_definition cascade;
        alter table location drop constraint if exists location_tenant_id_id_key;
    """)
