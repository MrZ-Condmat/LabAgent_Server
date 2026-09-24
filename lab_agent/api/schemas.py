"""Explicit public auth API payloads; no ORM objects or tokens in JSON."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from lab_agent.auth.models import CurrentUser
from lab_agent.db.models import UserRole


class RequestCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)


class RequestCodeResponse(BaseModel):
    challenge_id: UUID
    expires_at: datetime
    status: str = "verification_code_sent"


class VerifyCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    challenge_id: UUID
    code: str = Field(min_length=1, max_length=64)


class AuthenticatedUserResponse(BaseModel):
    id: UUID
    email: str
    display_name: str
    role: UserRole

    @classmethod
    def from_current_user(cls, user: CurrentUser) -> "AuthenticatedUserResponse":
        return cls(id=user.id, email=user.email, display_name=user.display_name, role=user.role)


class VerifyCodeResponse(BaseModel):
    authenticated: bool = True
    user: AuthenticatedUserResponse
    session_expires_at: datetime


class MeResponse(BaseModel):
    authenticated: bool = True
    user: AuthenticatedUserResponse
