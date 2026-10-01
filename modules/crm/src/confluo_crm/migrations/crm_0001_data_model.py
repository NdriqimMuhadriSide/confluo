"""CRM data model (ARCHITECTURE.md §3, "CRM module"), as the `crm` Alembic branch.

Tables are prefixed `crm_`; they reference core tables but never another module's.
Same rules as core: tenant_id default, standard RLS, composite (tenant_id, id)
foreign keys so nothing can point across tenants.

Revision ID: crm_0001
Revises:
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "crm_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("crm",)
depends_on: str | Sequence[str] | None = "0004_core_data_model"

TENANT_ID = "tenant_id uuid not null default app.current_tenant_id() references tenant(id) on delete cascade"
TIMESTAMPS = """
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
"""
ACTIVE_APPOINTMENT = "('pending_approval', 'confirmed')"


def tenant_rls(table: str, grants: str = "select, insert, update, delete") -> None:
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


TABLES = [
    "crm_channel_connection",
    "crm_conversation",
    "crm_message",
    "crm_call",
    "crm_customer_fact",
    "crm_match_candidate",
    "crm_service",
    "crm_service_field",
    "crm_resource",
    "crm_service_resource",
    "crm_availability_rule",
    "crm_availability_exception",
    "crm_appointment",
    "crm_calendar_connection",
    "crm_external_busy",
    "crm_reminder",
    "crm_knowledge_item",
    "crm_knowledge_chunk",
]


def upgrade() -> None:
    # --- Channels and conversations ------------------------------------------------------
    op.execute(f"""
        create table crm_channel_connection (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          channel text not null check (channel in
            ('web', 'whatsapp', 'email', 'phone', 'sms', 'instagram', 'messenger')),
          external_account_id text not null,
          -- Reference to a secret in the secret store, never the secret itself.
          credentials_ref text,
          status text not null default 'active' check (status in ('active', 'paused', 'error')),
          settings jsonb not null default '{{}}'::jsonb,
          {TIMESTAMPS},
          unique (tenant_id, id),
          -- An incoming webhook maps to exactly one tenant through this.
          unique (channel, external_account_id)
        );

        create table crm_conversation (
          id uuid primary key default gen_random_uuid(),  -- also the LangGraph thread id
          {TENANT_ID},
          customer_id uuid,
          channel text not null,
          channel_connection_id uuid,
          status text not null default 'open' check (status in ('open', 'waiting', 'closed')),
          handler text not null default 'ai' check (handler in ('ai', 'human')),
          assigned_member_id uuid references app_user(id) on delete set null,
          language text,
          last_message_at timestamptz,
          {TIMESTAMPS},
          unique (tenant_id, id),
          foreign key (tenant_id, customer_id) references customer (tenant_id, id) on delete set null (customer_id),
          foreign key (tenant_id, channel_connection_id)
            references crm_channel_connection (tenant_id, id) on delete set null (channel_connection_id)
        );
        create index crm_conversation_inbox_idx on crm_conversation (tenant_id, status, last_message_at desc);

        create table crm_message (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          conversation_id uuid not null,
          channel_connection_id uuid,
          direction text not null check (direction in ('inbound', 'outbound')),
          sender_type text not null check (sender_type in ('customer', 'ai', 'staff', 'system')),
          sender_member_id uuid references app_user(id) on delete set null,
          body text,
          content_type text not null default 'text/plain',
          attachments jsonb not null default '[]'::jsonb,
          external_id text,
          delivery_status text check (delivery_status in
            ('queued', 'sent', 'delivered', 'read', 'failed')),
          created_at timestamptz not null default now(),
          unique (tenant_id, id),
          foreign key (tenant_id, conversation_id)
            references crm_conversation (tenant_id, id) on delete cascade,
          foreign key (tenant_id, channel_connection_id)
            references crm_channel_connection (tenant_id, id) on delete set null (channel_connection_id)
        );
        create index crm_message_conversation_idx on crm_message (tenant_id, conversation_id, created_at);
        -- Provider message ids are unique per connection (idempotent ingestion).
        create unique index crm_message_external_idx on crm_message (channel_connection_id, external_id)
          where external_id is not null;

        create table crm_call (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          conversation_id uuid not null,
          mode text not null check (mode in ('receptionist', 'assist')),
          twilio_sid text unique,
          consent_played_at timestamptz,
          recording_ref text,
          transcript jsonb not null default '[]'::jsonb,
          duration_s int,
          created_at timestamptz not null default now(),
          foreign key (tenant_id, conversation_id)
            references crm_conversation (tenant_id, id) on delete cascade
        );
    """)

    # --- Customer intelligence ------------------------------------------------------------
    op.execute(f"""
        create table crm_customer_fact (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          customer_id uuid not null,
          key text not null,
          value jsonb not null,
          source_channel text,
          source_message_id uuid,
          extracted_by text not null check (extracted_by in ('ai', 'staff')),
          confidence real check (confidence between 0 and 1),
          status text not null default 'active'
            check (status in ('active', 'superseded', 'rejected', 'pending_review')),
          sensitivity text not null default 'personal'
            check (sensitivity in ('none', 'personal', 'sensitive')),
          created_at timestamptz not null default now(),
          foreign key (tenant_id, customer_id) references customer (tenant_id, id) on delete cascade,
          foreign key (tenant_id, source_message_id)
            references crm_message (tenant_id, id) on delete set null (source_message_id)
        );
        -- Facts are superseded, never overwritten: one active value per key.
        create unique index crm_customer_fact_active_idx on crm_customer_fact (tenant_id, customer_id, key)
          where status = 'active';

        create table crm_match_candidate (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          customer_a uuid not null,
          customer_b uuid not null,
          score real not null check (score between 0 and 1),
          signals jsonb not null default '{{}}'::jsonb,
          status text not null default 'pending' check (status in ('pending', 'merged', 'dismissed')),
          resolved_by uuid references app_user(id) on delete set null,
          {TIMESTAMPS},
          check (customer_a < customer_b),
          unique (tenant_id, customer_a, customer_b),
          foreign key (tenant_id, customer_a) references customer (tenant_id, id) on delete cascade,
          foreign key (tenant_id, customer_b) references customer (tenant_id, id) on delete cascade
        );
    """)

    # --- Services, resources, availability ---------------------------------------------------
    op.execute(f"""
        create table crm_service (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          name_i18n jsonb not null check (jsonb_typeof(name_i18n) = 'object' and name_i18n <> '{{}}'::jsonb),
          description_i18n jsonb not null default '{{}}'::jsonb,
          duration_min int not null check (duration_min between 5 and 1440),
          buffer_before_min int not null default 0 check (buffer_before_min between 0 and 240),
          buffer_after_min int not null default 0 check (buffer_after_min between 0 and 240),
          price_cents int check (price_cents >= 0),
          currency text not null default 'EUR',
          active boolean not null default true,
          position int not null default 0,
          {TIMESTAMPS},
          unique (tenant_id, id)
        );

        create table crm_service_field (
          {TENANT_ID},
          service_id uuid not null,
          field_definition_id uuid not null,
          required boolean not null default true,
          position int not null default 0,
          primary key (service_id, field_definition_id),
          foreign key (tenant_id, service_id) references crm_service (tenant_id, id) on delete cascade,
          foreign key (tenant_id, field_definition_id)
            references field_definition (tenant_id, id) on delete cascade
        );

        create table crm_resource (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          kind text not null check (kind in ('staff', 'room', 'equipment')),
          member_id uuid references app_user(id) on delete set null,
          location_id uuid,
          name text not null,
          active boolean not null default true,
          {TIMESTAMPS},
          unique (tenant_id, id),
          foreign key (tenant_id, location_id) references location (tenant_id, id) on delete set null (location_id)
        );

        create table crm_service_resource (
          {TENANT_ID},
          service_id uuid not null,
          resource_id uuid not null,
          primary key (service_id, resource_id),
          foreign key (tenant_id, service_id) references crm_service (tenant_id, id) on delete cascade,
          foreign key (tenant_id, resource_id) references crm_resource (tenant_id, id) on delete cascade
        );

        -- Recurring weekly schedule in the location's local time (weekday 1 = Monday).
        create table crm_availability_rule (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          resource_id uuid not null,
          weekday smallint not null check (weekday between 1 and 7),
          start_time time not null,
          end_time time not null,
          valid_from date,
          valid_to date,
          created_at timestamptz not null default now(),
          check (start_time < end_time),
          check (valid_to is null or valid_from is null or valid_from <= valid_to),
          foreign key (tenant_id, resource_id) references crm_resource (tenant_id, id) on delete cascade
        );
        create index crm_availability_rule_idx on crm_availability_rule (tenant_id, resource_id, weekday);

        create table crm_availability_exception (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          resource_id uuid not null,
          during tstzrange not null check (not isempty(during)),
          kind text not null check (kind in ('off', 'extra')),
          reason text,
          created_at timestamptz not null default now(),
          foreign key (tenant_id, resource_id) references crm_resource (tenant_id, id) on delete cascade
        );
        create index crm_availability_exception_idx on crm_availability_exception
          using gist (resource_id, during);
    """)

    # --- Appointments ---------------------------------------------------------------------
    op.execute(f"""
        create table crm_appointment (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          customer_id uuid not null,
          service_id uuid not null,
          resource_id uuid not null,
          during tstzrange not null check (not isempty(during)),
          status text not null default 'pending_approval' check (status in
            ('pending_approval', 'confirmed', 'cancelled', 'no_show', 'completed')),
          source text not null check (source in ('ai', 'staff', 'online')),
          field_values jsonb not null default '{{}}'::jsonb,
          conversation_id uuid,
          external_event_id text,
          {TIMESTAMPS},
          unique (tenant_id, id),
          foreign key (tenant_id, customer_id) references customer (tenant_id, id),
          foreign key (tenant_id, service_id) references crm_service (tenant_id, id),
          foreign key (tenant_id, resource_id) references crm_resource (tenant_id, id),
          foreign key (tenant_id, conversation_id)
            references crm_conversation (tenant_id, id) on delete set null (conversation_id),
          -- Double booking is impossible, even under concurrent requests.
          constraint crm_appointment_no_overlap exclude using gist
            (resource_id with =, during with &&) where (status in {ACTIVE_APPOINTMENT})
        );
        create index crm_appointment_customer_idx on crm_appointment (tenant_id, customer_id);
        create index crm_appointment_during_idx on crm_appointment using gist (tenant_id, during);

        create table crm_calendar_connection (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          resource_id uuid not null,
          provider text not null check (provider in ('google', 'microsoft')),
          calendar_id text not null,
          sync_token text,
          credentials_ref text,
          {TIMESTAMPS},
          unique (tenant_id, id),
          foreign key (tenant_id, resource_id) references crm_resource (tenant_id, id) on delete cascade
        );

        -- Cached free/busy from external calendars, subtracted from availability.
        create table crm_external_busy (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          resource_id uuid not null,
          calendar_connection_id uuid not null,
          during tstzrange not null,
          external_event_id text not null,
          unique (calendar_connection_id, external_event_id),
          foreign key (tenant_id, resource_id) references crm_resource (tenant_id, id) on delete cascade,
          foreign key (tenant_id, calendar_connection_id)
            references crm_calendar_connection (tenant_id, id) on delete cascade
        );
        create index crm_external_busy_idx on crm_external_busy using gist (resource_id, during);

        create table crm_reminder (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          appointment_id uuid not null,
          send_at timestamptz not null,
          channel text not null check (channel in ('email', 'sms', 'whatsapp')),
          status text not null default 'scheduled'
            check (status in ('scheduled', 'sent', 'failed', 'cancelled')),
          created_at timestamptz not null default now(),
          foreign key (tenant_id, appointment_id)
            references crm_appointment (tenant_id, id) on delete cascade
        );
        create index crm_reminder_due_idx on crm_reminder (send_at) where status = 'scheduled';
    """)

    # --- Knowledge base ----------------------------------------------------------------------
    op.execute(f"""
        create table crm_knowledge_item (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          kind text not null check (kind in
            ('faq', 'service', 'price', 'hours', 'location', 'policy', 'free_text')),
          title text not null,
          body text not null,
          language text not null check (language in ('en', 'nl', 'fr', 'de', 'sq')),
          published boolean not null default false,
          published_at timestamptz,
          embedded_at timestamptz,
          {TIMESTAMPS},
          unique (tenant_id, id)
        );

        -- Voyage voyage-3.5 embeddings (1024 dims). Full text uses the 'simple' config
        -- because content comes in five languages.
        create table crm_knowledge_chunk (
          id uuid primary key default gen_random_uuid(),
          {TENANT_ID},
          knowledge_item_id uuid not null,
          language text not null,
          position int not null,
          content text not null,
          embedding extensions.vector(1024),
          tsv tsvector generated always as (to_tsvector('simple', content)) stored,
          created_at timestamptz not null default now(),
          foreign key (tenant_id, knowledge_item_id)
            references crm_knowledge_item (tenant_id, id) on delete cascade
        );
        create index crm_knowledge_chunk_item_idx on crm_knowledge_chunk (tenant_id, knowledge_item_id);
        create index crm_knowledge_chunk_tsv_idx on crm_knowledge_chunk using gin (tsv);
        create index crm_knowledge_chunk_embedding_idx on crm_knowledge_chunk
          using hnsw (embedding extensions.vector_cosine_ops);
    """)

    for table in TABLES:
        tenant_rls(table)
    for table in (
        "crm_channel_connection",
        "crm_conversation",
        "crm_match_candidate",
        "crm_service",
        "crm_resource",
        "crm_appointment",
        "crm_calendar_connection",
        "crm_knowledge_item",
    ):
        updated_at(table)


def downgrade() -> None:
    op.execute("drop table if exists " + ", ".join(reversed(TABLES)) + " cascade")
