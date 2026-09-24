from unittest.mock import Mock
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from lab_agent.db.models import User
from lab_agent.repositories import UserRepository


def compile_postgresql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_user_lookup_queries_use_the_requested_identity_field():
    session = Mock(spec=Session)
    session.scalar.return_value = None
    repository = UserRepository(session)
    user_id = uuid4()

    assert repository.get_by_id(user_id) is None
    by_id_sql = compile_postgresql(session.scalar.call_args.args[0])
    assert "users.id" in by_id_sql
    assert str(user_id) in by_id_sql

    assert repository.get_by_email("MixedCase@Example.com") is None
    by_email_sql = compile_postgresql(session.scalar.call_args.args[0])
    assert "users.email = 'MixedCase@Example.com'" in by_email_sql

    assert repository.get_by_external_subject("oidc-subject") is None
    by_subject_sql = compile_postgresql(session.scalar.call_args.args[0])
    assert "users.external_subject = 'oidc-subject'" in by_subject_sql

    tenant_id = uuid4()
    object_id = uuid4()
    assert repository.get_by_tenant_object(tenant_id, object_id) is None
    by_tenant_object_sql = compile_postgresql(session.scalar.call_args.args[0])
    assert "users.tenant_id" in by_tenant_object_sql
    assert "users.external_object_id" in by_tenant_object_sql
    assert str(tenant_id) in by_tenant_object_sql
    assert str(object_id) in by_tenant_object_sql


def test_user_add_flushes_without_owning_the_transaction():
    session = Mock(spec=Session)
    repository = UserRepository(session)
    user = User(email="researcher@example.com", display_name="Researcher")

    assert repository.add(user) is user

    session.add.assert_called_once_with(user)
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()


def test_update_login_identity_changes_only_allowed_fields_and_flushes():
    session = Mock(spec=Session)
    repository = UserRepository(session)
    user = User(
        email="old@example.com",
        display_name="Old Name",
        external_subject=None,
        role="admin",
        is_active=True,
    )
    login_time = datetime.now(timezone.utc)

    result = repository.update_login_identity(
        user,
        external_subject="oidc-subject",
        tenant_id=uuid4(),
        external_object_id=uuid4(),
        email="new@example.com",
        display_name="New Name",
        last_login_at=login_time,
    )

    assert result is user
    assert user.external_subject == "oidc-subject"
    assert user.tenant_id is not None
    assert user.external_object_id is not None
    assert user.email == "new@example.com"
    assert user.display_name == "New Name"
    assert user.last_login_at is login_time
    assert user.role == "admin"
    assert user.is_active is True
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()


def test_update_last_login_preserves_identity_and_transaction_boundary():
    session = Mock(spec=Session)
    repository = UserRepository(session)
    user = User(email="user@example.com", display_name="Existing", external_subject="stable-sub",
                tenant_id=uuid4(), external_object_id=uuid4(), role="admin", is_active=True)
    original_identity = (user.email, user.external_subject, user.tenant_id, user.external_object_id, user.role)
    login_time = datetime.now(timezone.utc)

    assert repository.update_last_login(user, last_login_at=login_time) is user

    assert user.last_login_at is login_time
    assert (user.email, user.external_subject, user.tenant_id, user.external_object_id, user.role) == original_identity
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()
