from unittest.mock import Mock
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
