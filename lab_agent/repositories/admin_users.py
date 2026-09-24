"""Caller-owned persistence operations for administrator user management."""

from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from lab_agent.db.models import User, UserRole


# A transaction-scoped PostgreSQL lock serializes bootstrap and every role or
# active-state change. Locking only the currently active admin rows is not
# sufficient when two transactions start from different snapshots.
_ADMIN_MUTATION_LOCK_KEY = 0x4C41424147454E54


class AdminUserRepository:
    def __init__(self, session: Session):
        self.session = session

    def lock_admin_mutations(self) -> None:
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _ADMIN_MUTATION_LOCK_KEY},
        ).scalar_one()

    def list_users(self) -> list[User]:
        return list(self.session.scalars(select(User).order_by(User.created_at, User.id)).all())

    def get_by_id(self, user_id: UUID, *, for_update: bool = False) -> User | None:
        statement = select(User).where(User.id == user_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def get_by_email(self, email: str, *, for_update: bool = False) -> User | None:
        statement = select(User).where(User.email == email)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def active_admin_count(self) -> int:
        return int(
            self.session.scalar(
                select(func.count()).select_from(User).where(
                    User.role == UserRole.ADMIN, User.is_active.is_(True)
                )
            )
            or 0
        )

    def flush(self) -> None:
        self.session.flush()
