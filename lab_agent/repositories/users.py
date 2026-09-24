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

    def get_by_tenant_object(
        self,
        tenant_id: UUID,
        external_object_id: UUID,
    ) -> User | None:
        return self.session.scalar(
            select(User).where(
                User.tenant_id == tenant_id,
                User.external_object_id == external_object_id,
            )
        )

    def add(self, user: User) -> User:
        self.session.add(user)
        self.session.flush()
        return user

    def update_last_login(self, user: User, *, last_login_at: datetime) -> User:
        """Record a successful local login without changing linked identities."""
        user.last_login_at = last_login_at
        self.session.flush()
        return user

    def update_login_identity(
        self,
        user: User,
        *,
        external_subject: str,
        tenant_id: UUID | None,
        external_object_id: UUID | None,
        email: str,
        display_name: str,
        last_login_at: datetime,
    ) -> User:
        """Persist identity fields that a successful login is allowed to change."""
        user.external_subject = external_subject
        user.tenant_id = tenant_id
        user.external_object_id = external_object_id
        user.email = email
        user.display_name = display_name
        user.last_login_at = last_login_at
        self.session.flush()
        return user
