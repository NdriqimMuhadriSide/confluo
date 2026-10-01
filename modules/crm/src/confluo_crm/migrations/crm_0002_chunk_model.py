"""Record which embedding model produced each knowledge chunk.

Vectors from different models aren't comparable, so search only uses chunks of the
configured model; `make reembed` rebuilds them after a model change.

Revision ID: crm_0002
Revises: crm_0001
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "crm_0002"
down_revision: str | Sequence[str] | None = "crm_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        alter table crm_knowledge_chunk add column embedding_model text;
        create index crm_knowledge_chunk_model_idx on crm_knowledge_chunk (tenant_id, embedding_model);
    """)


def downgrade() -> None:
    op.execute("alter table crm_knowledge_chunk drop column if exists embedding_model")
