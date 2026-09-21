"""Data access for user records."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from lab_agent.db.models import User


class UserRepository:
    """Query and stage User records using a caller-owned Session."""

    def __init__(self, session: Session):
        self.session = session

    def get_by_id(self, user_id: UUID) -> User | None:
        return self.session.scalar(select(User).where(User.id == user_id))

    def get_by_email(self, email: str) -> User | None:
        return self.session.scalar(select(User).where(User.email == email))

    def get_by_external_subject(self, external_subject: str) -> User | None:
        return self.session.scalar(
            select(User).where(User.external_subject == external_subject)
        )

    def add(self, user: User) -> User:
        self.session.add(user)
        self.session.flush()
        return user

    def update_login_identity(
        self,
        user: User,
        *,
        external_subject: str,
        email: str,
        display_name: str,
        last_login_at: datetime,
    ) -> User:
        """Persist identity fields that a successful login is allowed to change."""
        user.external_subject = external_subject
        user.email = email
        user.display_name = display_name
        user.last_login_at = last_login_at
        self.session.flush()
        return user
