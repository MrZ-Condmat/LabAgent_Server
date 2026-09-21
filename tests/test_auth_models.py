from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from lab_agent.auth import CurrentUser, IdentityClaims
from lab_agent.db.models import User, UserRole


def test_current_user_is_immutable_and_separate_from_orm_user():
    tenant_id = uuid4()
    object_id = uuid4()
    user = User(
        id=uuid4(),
        external_subject="provider-subject",
        tenant_id=tenant_id,
        external_object_id=object_id,
        email="user@example.com",
        display_name="User",
        role=UserRole.USER,
        is_active=True,
    )

    current_user = CurrentUser.from_user(user)

    assert current_user.id == user.id
    assert current_user.external_subject == "provider-subject"
    assert current_user.tenant_id == tenant_id
    assert current_user.external_object_id == object_id
    assert current_user.email == "user@example.com"
    assert current_user.display_name == "User"
    assert current_user.role is UserRole.USER
    assert current_user.is_active is True
    assert not hasattr(current_user, "access_token")
    assert not hasattr(current_user, "password")
    with pytest.raises(FrozenInstanceError):
        current_user.role = UserRole.ADMIN


def test_identity_claims_are_provider_neutral_and_immutable():
    tenant_id = uuid4()
    object_id = uuid4()
    claims = IdentityClaims(
        subject="subject",
        tenant_id=tenant_id,
        object_id=object_id,
        email="user@example.com",
        display_name="User",
    )

    assert claims.subject == "subject"
    assert claims.tenant_id == tenant_id
    assert claims.object_id == object_id
    assert claims.email == "user@example.com"
    assert claims.display_name == "User"
    assert not hasattr(claims, "role")
    assert not hasattr(claims, "id_token")
    with pytest.raises(FrozenInstanceError):
        claims.email = "changed@example.com"
