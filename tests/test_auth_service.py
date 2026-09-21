from datetime import timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest

from lab_agent.auth import (
    AuthenticationService,
    IdentityClaims,
    IdentityConflictError,
    InvalidIdentityError,
    UnknownUserError,
    UserInactiveError,
)
from lab_agent.db.models import User, UserRole
from lab_agent.repositories import UserRepository


def make_user(
    *,
    external_subject="subject",
    tenant_id=None,
    external_object_id=None,
    email="user@example.com",
    display_name="Existing User",
    role=UserRole.USER,
    is_active=True,
):
    return User(
        id=uuid4(),
        external_subject=external_subject,
        tenant_id=tenant_id,
        external_object_id=external_object_id,
        email=email,
        display_name=display_name,
        role=role,
        is_active=is_active,
    )


def repository_mock() -> Mock:
    repository = Mock(spec=UserRepository)

    def update_identity(user, **values):
        for field, value in values.items():
            setattr(user, field, value)
        return user

    def add_user(user):
        if user.id is None:
            user.id = uuid4()
        return user

    repository.update_login_identity.side_effect = update_identity
    repository.add.side_effect = add_user
    return repository


def claims(
    subject="subject",
    tenant_id=None,
    object_id=None,
    email="user@example.com",
    display_name="Provider User",
):
    return IdentityClaims(
        subject=subject,
        tenant_id=tenant_id,
        object_id=object_id,
        email=email,
        display_name=display_name,
    )


def test_bound_subject_is_primary_and_syncs_allowed_identity_fields():
    repository = repository_mock()
    user = make_user(
        email="old@example.com",
        display_name="Old Name",
        role=UserRole.ADMIN,
    )
    repository.get_by_external_subject.return_value = user
    repository.get_by_tenant_object.return_value = None
    repository.get_by_email.return_value = None
    service = AuthenticationService(repository)

    current_user = service.resolve_user(
        claims(
            subject="  subject  ",
            email=" New.User@Lab.EDU ",
            display_name="  New Name  ",
        )
    )

    repository.get_by_external_subject.assert_called_once_with("subject")
    repository.get_by_email.assert_called_once_with("new.user@lab.edu")
    repository.add.assert_not_called()
    update = repository.update_login_identity.call_args
    assert update.args == (user,)
    assert update.kwargs["external_subject"] == "subject"
    assert update.kwargs["tenant_id"] is None
    assert update.kwargs["external_object_id"] is None
    assert update.kwargs["email"] == "new.user@lab.edu"
    assert update.kwargs["display_name"] == "New Name"
    assert update.kwargs["last_login_at"].tzinfo is timezone.utc
    assert current_user.id == user.id
    assert current_user.email == "new.user@lab.edu"
    assert current_user.role is UserRole.ADMIN
    assert user.role is UserRole.ADMIN
    assert user.is_active is True


def test_first_login_binds_subject_to_precreated_email_user():
    repository = repository_mock()
    user = make_user(external_subject=None, email="member@lab.edu")
    tenant_id = uuid4()
    object_id = uuid4()
    repository.get_by_external_subject.return_value = None
    repository.get_by_tenant_object.return_value = None
    repository.get_by_email.return_value = user
    service = AuthenticationService(repository)

    current_user = service.resolve_user(
        claims(
            subject="oidc-123",
            tenant_id=tenant_id,
            object_id=object_id,
            email=" MEMBER@LAB.EDU ",
        )
    )

    assert current_user.id == user.id
    assert current_user.external_subject == "oidc-123"
    assert current_user.tenant_id == tenant_id
    assert current_user.external_object_id == object_id
    assert user.external_subject == "oidc-123"
    assert user.tenant_id == tenant_id
    assert user.external_object_id == object_id
    repository.add.assert_not_called()


def test_unknown_user_is_rejected_by_default():
    repository = repository_mock()
    repository.get_by_external_subject.return_value = None
    repository.get_by_tenant_object.return_value = None
    repository.get_by_email.return_value = None

    with pytest.raises(UnknownUserError, match="^User is not authorized$"):
        AuthenticationService(repository).resolve_user(claims())

    repository.add.assert_not_called()
    repository.update_login_identity.assert_not_called()


def test_auto_provision_creates_only_active_regular_user():
    repository = repository_mock()
    repository.get_by_external_subject.return_value = None
    repository.get_by_tenant_object.return_value = None
    repository.get_by_email.return_value = None
    service = AuthenticationService(repository, allow_auto_provision=True)
    tenant_id = uuid4()
    object_id = uuid4()

    current_user = service.resolve_user(
        claims(
            subject=" new-subject ",
            tenant_id=tenant_id,
            object_id=object_id,
            email=" New@Lab.EDU ",
            display_name=" ",
        )
    )

    created_user = repository.add.call_args.args[0]
    assert created_user.external_subject == "new-subject"
    assert created_user.tenant_id == tenant_id
    assert created_user.external_object_id == object_id
    assert created_user.email == "new@lab.edu"
    assert created_user.display_name == "new@lab.edu"
    assert created_user.role is UserRole.USER
    assert created_user.is_active is True
    assert created_user.last_login_at.tzinfo is timezone.utc
    assert current_user.role is UserRole.USER
    assert current_user.tenant_id == tenant_id
    assert current_user.external_object_id == object_id
    repository.update_login_identity.assert_not_called()


def test_subject_and_tenant_object_matching_same_user_resolve_normally():
    repository = repository_mock()
    tenant_id = uuid4()
    object_id = uuid4()
    user = make_user(tenant_id=tenant_id, external_object_id=object_id)
    repository.get_by_external_subject.return_value = user
    repository.get_by_tenant_object.return_value = user
    repository.get_by_email.return_value = user

    current_user = AuthenticationService(repository).resolve_user(
        claims(tenant_id=tenant_id, object_id=object_id)
    )

    assert current_user.id == user.id
    repository.get_by_tenant_object.assert_called_once_with(tenant_id, object_id)


def test_subject_and_tenant_object_pointing_to_different_users_is_rejected():
    repository = repository_mock()
    tenant_id = uuid4()
    object_id = uuid4()
    repository.get_by_external_subject.return_value = make_user()
    repository.get_by_tenant_object.return_value = make_user(
        external_subject=None,
        tenant_id=tenant_id,
        external_object_id=object_id,
    )
    repository.get_by_email.return_value = None

    with pytest.raises(IdentityConflictError):
        AuthenticationService(repository).resolve_user(
            claims(tenant_id=tenant_id, object_id=object_id)
        )


def test_tenant_object_and_email_pointing_to_different_users_is_rejected():
    repository = repository_mock()
    tenant_id = uuid4()
    object_id = uuid4()
    repository.get_by_external_subject.return_value = None
    repository.get_by_tenant_object.return_value = make_user(
        external_subject=None,
        tenant_id=tenant_id,
        external_object_id=object_id,
        email="tenant-owner@example.com",
    )
    repository.get_by_email.return_value = make_user(
        external_subject=None,
        email="user@example.com",
    )

    with pytest.raises(IdentityConflictError):
        AuthenticationService(repository).resolve_user(
            claims(tenant_id=tenant_id, object_id=object_id)
        )


def test_subject_change_is_rejected_even_when_tenant_object_matches():
    repository = repository_mock()
    tenant_id = uuid4()
    object_id = uuid4()
    user = make_user(
        external_subject="old-subject",
        tenant_id=tenant_id,
        external_object_id=object_id,
    )
    repository.get_by_external_subject.return_value = None
    repository.get_by_tenant_object.return_value = user
    repository.get_by_email.return_value = user

    with pytest.raises(IdentityConflictError):
        AuthenticationService(repository).resolve_user(
            claims(
                subject="new-subject",
                tenant_id=tenant_id,
                object_id=object_id,
            )
        )


def test_provider_neutral_claims_do_not_clear_existing_tenant_identity():
    repository = repository_mock()
    tenant_id = uuid4()
    object_id = uuid4()
    user = make_user(tenant_id=tenant_id, external_object_id=object_id)
    repository.get_by_external_subject.return_value = user
    repository.get_by_email.return_value = user

    current_user = AuthenticationService(repository).resolve_user(claims())

    assert current_user.tenant_id == tenant_id
    assert current_user.external_object_id == object_id
    update = repository.update_login_identity.call_args.kwargs
    assert update["tenant_id"] == tenant_id
    assert update["external_object_id"] == object_id


@pytest.mark.parametrize("match_by", ["subject", "email"])
def test_inactive_user_is_rejected_without_identity_updates(match_by):
    repository = repository_mock()
    user = make_user(
        external_subject="subject" if match_by == "subject" else None,
        is_active=False,
    )
    repository.get_by_external_subject.return_value = (
        user if match_by == "subject" else None
    )
    repository.get_by_tenant_object.return_value = None
    repository.get_by_email.return_value = user

    with pytest.raises(UserInactiveError, match="^User account is inactive$"):
        AuthenticationService(repository).resolve_user(claims())

    repository.update_login_identity.assert_not_called()
    repository.add.assert_not_called()


def test_subject_and_email_pointing_to_different_users_is_rejected():
    repository = repository_mock()
    repository.get_by_external_subject.return_value = make_user(
        email="subject-owner@example.com"
    )
    repository.get_by_tenant_object.return_value = None
    repository.get_by_email.return_value = make_user(
        external_subject=None,
        email="user@example.com",
    )

    with pytest.raises(
        IdentityConflictError,
        match="^Identity claims conflict with an account$",
    ):
        AuthenticationService(repository).resolve_user(claims())

    repository.update_login_identity.assert_not_called()
    repository.add.assert_not_called()


def test_email_account_bound_to_another_subject_is_rejected():
    repository = repository_mock()
    repository.get_by_external_subject.return_value = None
    repository.get_by_tenant_object.return_value = None
    repository.get_by_email.return_value = make_user(
        external_subject="different-subject"
    )

    with pytest.raises(IdentityConflictError):
        AuthenticationService(repository).resolve_user(claims(subject="new-subject"))


@pytest.mark.parametrize(
    "invalid_claims",
    [
        claims(subject="   "),
        claims(email=""),
        claims(email="not-an-email"),
        claims(email="two@@example.com"),
        claims(email="user @example.com"),
        claims(tenant_id=uuid4(), object_id=None),
        claims(tenant_id=None, object_id=uuid4()),
        claims(tenant_id="not-a-uuid", object_id=uuid4()),
    ],
)
def test_invalid_identity_is_rejected_before_repository_lookup(invalid_claims):
    repository = repository_mock()

    with pytest.raises(InvalidIdentityError):
        AuthenticationService(repository).resolve_user(invalid_claims)

    repository.get_by_external_subject.assert_not_called()
    repository.get_by_tenant_object.assert_not_called()
    repository.get_by_email.assert_not_called()
