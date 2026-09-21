"""Add Microsoft Entra identity fields to users.

Revision ID: 0003_entra_identity
Revises: 0002_conversations_messages
Create Date: 2026-09-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_entra_identity"
down_revision: str | None = "0002_conversations_messages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable tenant/object identity and its composite uniqueness rule."""
    op.add_column("users", sa.Column("tenant_id", sa.Uuid(), nullable=True))
    op.add_column(
        "users",
        sa.Column("external_object_id", sa.Uuid(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_users_tenant_object",
        "users",
        ["tenant_id", "external_object_id"],
    )


def downgrade() -> None:
    """Remove the Entra tenant/object identity fields."""
    op.drop_constraint(
        "uq_users_tenant_object",
        "users",
        type_="unique",
    )
    op.drop_column("users", "external_object_id")
    op.drop_column("users", "tenant_id")
