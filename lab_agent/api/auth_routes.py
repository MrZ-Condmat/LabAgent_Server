"""JSON authentication routes; raw tokens only leave via Set-Cookie."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.responses import JSONResponse

from lab_agent.auth.email_otp import (
    AuthConfigurationError,
    EmailDomainNotAllowedError,
    OtpInvalidError,
    OtpRateLimitError,
)
from lab_agent.auth.email_sender import EmailDeliveryError
from lab_agent.auth.errors import UserInactiveError
from lab_agent.auth.smtp_sender import SmtpEmailSender
from lab_agent.db.session import SessionFactory, database_session
from lab_agent.utils.config import Config

from .cookie import clear_session_cookie, cookie_name, set_session_cookie
from .dependencies import (
    build_authentication_service,
    build_delivery_service,
    build_session_service,
    get_auth_config,
    get_db_session_factory,
    get_email_sender,
)
from .schemas import (
    AuthenticatedUserResponse,
    MeResponse,
    RequestCodeRequest,
    RequestCodeResponse,
    VerifyCodeRequest,
    VerifyCodeResponse,
)


router = APIRouter(prefix="/auth")
_INVALID_CODE = "Invalid or expired verification code."
_SEND_FAILED = "Unable to send verification email."


@router.post("/request-code", status_code=202, response_model=RequestCodeResponse)
def request_code(
    payload: RequestCodeRequest,
    factory: SessionFactory = Depends(get_db_session_factory),
    config: Config = Depends(get_auth_config),
    sender: SmtpEmailSender = Depends(get_email_sender),
) -> RequestCodeResponse:
    try:
        with database_session(factory) as session:
            delivery = build_delivery_service(session, sender, config)
            result = delivery.request_verification_email(payload.email)
    except EmailDomainNotAllowedError:
        raise HTTPException(status_code=400, detail="Email address is not allowed.") from None
    except OtpRateLimitError:
        raise HTTPException(status_code=429, detail="Verification code request limit reached.") from None
    except (EmailDeliveryError, AuthConfigurationError):
        raise HTTPException(status_code=503, detail=_SEND_FAILED) from None
    return RequestCodeResponse(challenge_id=result.challenge_id, expires_at=result.expires_at)


@router.post("/verify-code", response_model=VerifyCodeResponse)
def verify_code(
    payload: VerifyCodeRequest,
    response: Response,
    factory: SessionFactory = Depends(get_db_session_factory),
    config: Config = Depends(get_auth_config),
) -> VerifyCodeResponse:
    try:
        with database_session(factory) as session:
            attempt = build_authentication_service(session, config).authenticate(
                challenge_id=payload.challenge_id, email=payload.email, code=payload.code
            )
            # Wrong code returns normally, so database_session commits failed_attempts.
    except (OtpInvalidError, EmailDomainNotAllowedError):
        raise HTTPException(status_code=401, detail=_INVALID_CODE) from None
    except UserInactiveError:
        raise HTTPException(status_code=403, detail="This account is disabled.") from None
    except AuthConfigurationError:
        raise HTTPException(status_code=503, detail="Authentication is unavailable.") from None

    if not attempt.succeeded:
        raise HTTPException(status_code=401, detail=_INVALID_CODE)
    authenticated = attempt.authentication
    set_session_cookie(response, token=authenticated.session_token,
                       expires_at=authenticated.expires_at, config=config)
    return VerifyCodeResponse(
        user=AuthenticatedUserResponse.from_current_user(authenticated.current_user),
        session_expires_at=authenticated.expires_at,
    )


@router.get("/me", response_model=MeResponse)
def me(
    request: Request,
    factory: SessionFactory = Depends(get_db_session_factory),
    config: Config = Depends(get_auth_config),
) -> MeResponse | JSONResponse:
    token = request.cookies.get(cookie_name(config))
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    try:
        with database_session(factory) as session:
            user = build_session_service(session, config).validate_session(token)
    except AuthConfigurationError:
        raise HTTPException(status_code=503, detail="Authentication is unavailable.") from None
    if user is None:
        response = JSONResponse({"detail": "Not authenticated."}, status_code=401)
        clear_session_cookie(response, config=config)
        return response
    return MeResponse(user=AuthenticatedUserResponse.from_current_user(user))


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    factory: SessionFactory = Depends(get_db_session_factory),
    config: Config = Depends(get_auth_config),
) -> Response:
    token = request.cookies.get(cookie_name(config))
    if token:
        try:
            with database_session(factory) as session:
                build_session_service(session, config).revoke_session(token)
        except AuthConfigurationError:
            raise HTTPException(status_code=503, detail="Authentication is unavailable.") from None
    response = Response(status_code=204)
    clear_session_cookie(response, config=config)
    return response
