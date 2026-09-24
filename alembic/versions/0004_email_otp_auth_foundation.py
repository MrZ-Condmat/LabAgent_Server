"""Create email OTP challenges and revocable user sessions.

Revision ID: 0004_email_otp_auth_foundation
Revises: 0003_entra_identity
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0004_email_otp_auth_foundation"
down_revision: str | None = "0003_entra_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_login_challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("code_hash", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("failed_attempts >= 0", name="ck_email_challenges_attempts"),
        sa.PrimaryKeyConstraint("id", name="pk_email_login_challenges"),
    )
    op.create_index("ix_email_challenges_email_created", "email_login_challenges", ["email", "created_at"])
    op.create_index("ix_email_challenges_expires", "email_login_challenges", ["expires_at"])
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_sessions_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_user_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_user_sessions_token_hash"),
    )


def downgrade() -> None:
    op.drop_table("user_sessions")
    op.drop_index("ix_email_challenges_expires", table_name="email_login_challenges")
    op.drop_index("ix_email_challenges_email_created", table_name="email_login_challenges")
    op.drop_table("email_login_challenges")
