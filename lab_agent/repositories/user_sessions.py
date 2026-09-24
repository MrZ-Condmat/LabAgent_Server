"""Storage for hashed, revocable user sessions."""

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from lab_agent.db.models import UserSession


class UserSessionRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self, user_session: UserSession) -> UserSession:
        self.session.add(user_session)
        self.session.flush()
        return user_session

    def get_active_by_token_hash(self, token_hash: str, now: datetime) -> UserSession | None:
        return self.session.scalar(
            select(UserSession).where(UserSession.token_hash == token_hash,
                                      UserSession.revoked_at.is_(None),
                                      UserSession.expires_at > now)
        )

    def revoke(self, user_session: UserSession, now: datetime) -> None:
        user_session.revoked_at = now
        self.session.flush()

    def revoke_all_for_user(self, user_id: uuid.UUID, now: datetime) -> None:
        self.session.execute(update(UserSession).where(UserSession.user_id == user_id,
                                                       UserSession.revoked_at.is_(None)).values(revoked_at=now))

    def touch(self, user_session: UserSession, now: datetime) -> None:
        user_session.last_seen_at = now
        self.session.flush()
