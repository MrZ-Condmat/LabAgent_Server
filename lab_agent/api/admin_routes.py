"""Administrator user management endpoints backed by server-side authorization."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool

from lab_agent.auth.admin_users import (
    AdminSafetyError,
    AdminUserNotFoundError,
    AdminUserService,
    AdminUserSummary,
)
from lab_agent.auth.authorization import AdminAuthorizationError
from lab_agent.auth.models import CurrentUser
from lab_agent.db.models import UserRole
from lab_agent.db.session import SessionFactory, database_session
from lab_agent.repositories.admin_users import AdminUserRepository

from .dependencies import get_db_session_factory, require_admin_user


router = APIRouter(prefix="/admin", tags=["admin"])


class AdminUserResponse(BaseModel):
    id: UUID
    email: str
    display_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None

    @classmethod
    def from_summary(cls, user: AdminUserSummary) -> "AdminUserResponse":
        return cls(
            id=user.id, email=user.email, display_name=user.display_name,
            role=user.role, is_active=user.is_active,
            created_at=user.created_at, last_login_at=user.last_login_at,
        )


class ChangeRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: UserRole


class ChangeActiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: StrictBool


def _handle_admin_error(exc: Exception) -> None:
    if isinstance(exc, AdminAuthorizationError):
        raise HTTPException(status_code=403, detail="Administrator access required.") from None
    if isinstance(exc, AdminUserNotFoundError):
        raise HTTPException(status_code=404, detail="User not found.") from None
    if isinstance(exc, AdminSafetyError):
        raise HTTPException(status_code=409, detail=str(exc)) from None
    raise exc


@router.get("/users", response_model=list[AdminUserResponse])
def list_users(
    actor: CurrentUser = Depends(require_admin_user),
    factory: SessionFactory = Depends(get_db_session_factory),
) -> list[AdminUserResponse]:
    try:
        with database_session(factory) as session:
            result = AdminUserService(AdminUserRepository(session)).list_users(actor=actor)
    except (AdminAuthorizationError, AdminUserNotFoundError, AdminSafetyError) as exc:
        _handle_admin_error(exc)
    return [AdminUserResponse.from_summary(user) for user in result]


@router.patch("/users/{user_id}/role", response_model=AdminUserResponse)
def change_user_role(
    user_id: UUID,
    payload: ChangeRoleRequest,
    actor: CurrentUser = Depends(require_admin_user),
    factory: SessionFactory = Depends(get_db_session_factory),
) -> AdminUserResponse:
    try:
        with database_session(factory) as session:
            result = AdminUserService(AdminUserRepository(session)).change_user_role(
                actor=actor, target_user_id=user_id, new_role=payload.role
            )
    except (AdminAuthorizationError, AdminUserNotFoundError, AdminSafetyError) as exc:
        _handle_admin_error(exc)
    return AdminUserResponse.from_summary(result)


@router.patch("/users/{user_id}/active", response_model=AdminUserResponse)
def set_user_active(
    user_id: UUID,
    payload: ChangeActiveRequest,
    actor: CurrentUser = Depends(require_admin_user),
    factory: SessionFactory = Depends(get_db_session_factory),
) -> AdminUserResponse:
    try:
        with database_session(factory) as session:
            result = AdminUserService(AdminUserRepository(session)).set_user_active(
                actor=actor, target_user_id=user_id, is_active=payload.is_active
            )
    except (AdminAuthorizationError, AdminUserNotFoundError, AdminSafetyError) as exc:
        _handle_admin_error(exc)
    return AdminUserResponse.from_summary(result)
