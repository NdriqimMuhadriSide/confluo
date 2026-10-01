"""Schema `langgraph` for the intake graph's Postgres checkpointer.

Checkpoints (thread_id = conversation id) let a conversation resume on any worker,
days later. LangGraph's tables are unqualified, so they live in their own schema
(public keeps "every table has RLS"); confluo_app reaches them through its role
search_path. The SQL is vendored (sql/langgraph_checkpoints.sql) and the version
rows are recorded, so the library's own setup() finds nothing to do.

Checkpoints hold conversation content: erasing a customer must also delete the
threads of their conversations (GDPR card).

Revision ID: crm_0003
Revises: crm_0002
Create Date: 2026-10-01
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "crm_0003"
down_revision: str | Sequence[str] | None = "crm_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = "0007_job_queue"

SQL = Path(__file__).parent / "sql" / "langgraph_checkpoints.sql"


def upgrade() -> None:
    statements = [s.strip() for s in SQL.read_text().split("-- next --")[1:]]
    op.execute("create schema langgraph")
    op.execute("set local search_path = langgraph")
    for version, statement in enumerate(statements):
        op.execute(statement)
        op.execute(f"insert into checkpoint_migrations (v) values ({version})")
    op.execute("set local search_path = public")
    op.execute("""
        grant usage on schema langgraph to confluo_app;
        grant select, insert, update, delete on all tables in schema langgraph to confluo_app;
        alter role confluo_app set search_path = "$user", public, procrastinate, langgraph, extensions;
    """)


def downgrade() -> None:
    op.execute("""
        alter role confluo_app set search_path = "$user", public, procrastinate, extensions;
        drop schema if exists langgraph cascade;
    """)
