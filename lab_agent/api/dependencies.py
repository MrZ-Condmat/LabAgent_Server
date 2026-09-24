"""Lazy request-scoped wiring of existing auth and database services."""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from lab_agent.auth.email_otp import EmailOtpService
from lab_agent.auth.email_otp_authentication import EmailOtpAuthenticationService
from lab_agent.auth.email_otp_delivery import EmailOtpDeliveryService
from lab_agent.auth.authorization import AdminAuthorizationError, require_admin
from lab_agent.auth.models import CurrentUser
from lab_agent.auth.email_sender import EmailSenderConfigurationError
from lab_agent.auth.sessions import SessionService
from lab_agent.auth.smtp_sender import SmtpEmailSender
from lab_agent.db.session import SessionFactory, database_session, get_session_factory
from lab_agent.repositories.email_login_challenges import EmailLoginChallengeRepository
from lab_agent.repositories.user_sessions import UserSessionRepository
from lab_agent.repositories.users import UserRepository
from lab_agent.utils.config import Config

from .cookie import cookie_name


def get_auth_config() -> Config:
    return Config()


def get_db_session_factory() -> SessionFactory:
    return get_session_factory()


def get_email_sender(config: Config = Depends(get_auth_config)) -> SmtpEmailSender:
    try:
        return SmtpEmailSender(config)
    except EmailSenderConfigurationError:
        raise HTTPException(status_code=503, detail="Unable to send verification email.") from None


def build_delivery_service(session: Session, sender: SmtpEmailSender, config: Config) -> EmailOtpDeliveryService:
    return EmailOtpDeliveryService(EmailOtpService(EmailLoginChallengeRepository(session), config), sender)


def build_session_service(session: Session, config: Config) -> SessionService:
    users = UserRepository(session)
    return SessionService(UserSessionRepository(session), users, config)


def build_authentication_service(session: Session, config: Config) -> EmailOtpAuthenticationService:
    users = UserRepository(session)
    return EmailOtpAuthenticationService(
        EmailOtpService(EmailLoginChallengeRepository(session), config),
        users,
        SessionService(UserSessionRepository(session), users, config),
    )


def require_current_user(
    request: Request,
    factory: SessionFactory = Depends(get_db_session_factory),
    config: Config = Depends(get_auth_config),
) -> CurrentUser:
    """Resolve the opaque HttpOnly cookie through the persisted session."""
    token = request.cookies.get(cookie_name(config))
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    with database_session(factory) as session:
        user = build_session_service(session, config).validate_session(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    return user


def require_admin_user(current_user: CurrentUser = Depends(require_current_user)) -> CurrentUser:
    try:
        return require_admin(current_user)
    except AdminAuthorizationError:
        raise HTTPException(status_code=403, detail="Administrator access required.") from None
