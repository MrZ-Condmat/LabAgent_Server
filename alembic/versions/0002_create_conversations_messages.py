"""Create conversations and messages.

Revision ID: 0002_conversations_messages
Revises: 0001_create_users
Create Date: 2026-09-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0002_conversations_messages"
down_revision: str | None = "0001_create_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create private conversations and their ordered messages."""
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "title",
            sa.String(length=255),
            server_default=sa.text("'New conversation'"),
            nullable=False,
        ),
        sa.Column(
            "conversation_type",
            sa.Enum(
                "overview",
                "arxiv",
                "journal",
                "general",
                "database",
                name="conversation_type",
                native_enum=False,
                create_constraint=False,
                length=16,
            ),
            server_default=sa.text("'general'"),
            nullable=False,
        ),
        sa.Column(
            "context_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "conversation_type IN "
            "('overview', 'arxiv', 'journal', 'general', 'database')",
            name="ck_conversations_type",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_conversations_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
    )
    op.create_index(
        "ix_conversations_user_id_updated_at",
        "conversations",
        ["user_id", "updated_at"],
        unique=False,
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "user",
                "assistant",
                "system",
                "tool",
                name="message_role",
                native_enum=False,
                create_constraint=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system', 'tool')",
            name="ck_messages_role",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_messages_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence_number",
            name="uq_messages_conversation_sequence",
        ),
    )


def downgrade() -> None:
    """Remove messages before their parent conversations."""
    op.drop_table("messages")
    op.drop_index(
        "ix_conversations_user_id_updated_at",
        table_name="conversations",
    )
    op.drop_table("conversations")
