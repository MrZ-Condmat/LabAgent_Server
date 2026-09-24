"""Transaction-scoped storage for OTP challenges."""

import uuid
from datetime import datetime

from sqlalchemy import select, update, func
from sqlalchemy.orm import Session

from lab_agent.db.models import EmailLoginChallenge


class EmailLoginChallengeRepository:
    def __init__(self, session: Session):
        self.session = session

    def lock_email(self, email: str) -> None:
        # Transaction advisory lock serializes requests even before a row exists.
        self.session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(email, 0))))

    def list_recent_for_email(self, email: str, since: datetime) -> list[EmailLoginChallenge]:
        statement = (select(EmailLoginChallenge)
                     .where(EmailLoginChallenge.email == email, EmailLoginChallenge.created_at >= since)
                     .order_by(EmailLoginChallenge.created_at.desc(), EmailLoginChallenge.id.desc()))
        return list(self.session.scalars(statement).all())

    def invalidate_active_for_email(self, email: str, now: datetime) -> None:
        self.session.execute(
            update(EmailLoginChallenge)
            .where(EmailLoginChallenge.email == email,
                   EmailLoginChallenge.consumed_at.is_(None),
                   EmailLoginChallenge.invalidated_at.is_(None),
                   EmailLoginChallenge.expires_at > now)
            .values(invalidated_at=now)
        )

    def create(self, challenge: EmailLoginChallenge) -> EmailLoginChallenge:
        self.session.add(challenge)
        self.session.flush()
        return challenge

    def get_for_update(self, challenge_id: uuid.UUID) -> EmailLoginChallenge | None:
        return self.session.scalar(
            select(EmailLoginChallenge).where(EmailLoginChallenge.id == challenge_id).with_for_update()
        )

    def record_failed_attempt(self, challenge: EmailLoginChallenge) -> None:
        challenge.failed_attempts += 1
        self.session.flush()

    def consume(self, challenge: EmailLoginChallenge, now: datetime) -> None:
        challenge.consumed_at = now
        self.session.flush()
