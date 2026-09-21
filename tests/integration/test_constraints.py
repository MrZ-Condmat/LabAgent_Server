"""Database-enforced constraints verified against PostgreSQL."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


pytestmark = pytest.mark.integration


def assert_insert_fails(engine, statement: str, parameters: dict) -> None:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(IntegrityError):
                connection.execute(text(statement), parameters)
        finally:
            transaction.rollback()


def test_role_and_type_check_constraints(integration_engine, clean_business_tables):
    del clean_business_tables
    user_id = uuid4()
    conversation_id = uuid4()
    with integration_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, display_name) "
                "VALUES (:id, :email, :name)"
            ),
            {"id": user_id, "email": "checks@example.com", "name": "Checks"},
        )
        connection.execute(
            text(
                "INSERT INTO conversations (id, user_id) "
                "VALUES (:id, :user_id)"
            ),
            {"id": conversation_id, "user_id": user_id},
        )

    assert_insert_fails(
        integration_engine,
        "INSERT INTO users (id, email, display_name, role) "
        "VALUES (:id, :email, :name, 'superadmin')",
        {"id": uuid4(), "email": "invalid-role@example.com", "name": "Invalid"},
    )
    assert_insert_fails(
        integration_engine,
        "INSERT INTO conversations (id, user_id, conversation_type) "
        "VALUES (:id, :user_id, 'invalid')",
        {"id": uuid4(), "user_id": user_id},
    )
    assert_insert_fails(
        integration_engine,
        "INSERT INTO messages "
        "(id, conversation_id, sequence_number, role, content) "
        "VALUES (:id, :conversation_id, 1, 'invalid', 'content')",
        {"id": uuid4(), "conversation_id": conversation_id},
    )


def test_user_unique_constraints_and_nullable_external_subject(
    integration_engine,
    clean_business_tables,
):
    del clean_business_tables
    with integration_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, external_subject, tenant_id, external_object_id, email, display_name) "
                "VALUES (:id, :subject, :tenant_id, :object_id, :email, :name)"
            ),
            {
                "id": uuid4(),
                "subject": "shared-subject",
                "tenant_id": uuid4(),
                "object_id": uuid4(),
                "email": "unique@example.com",
                "name": "First",
            },
        )

    assert_insert_fails(
        integration_engine,
        "INSERT INTO users (id, email, display_name) "
        "VALUES (:id, 'unique@example.com', 'Duplicate email')",
        {"id": uuid4()},
    )
    assert_insert_fails(
        integration_engine,
        "INSERT INTO users (id, external_subject, email, display_name) "
        "VALUES (:id, 'shared-subject', :email, 'Duplicate subject')",
        {"id": uuid4(), "email": "second@example.com"},
    )

    with integration_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, external_subject, email, display_name) "
                "VALUES (:id, NULL, :email, :name)"
            ),
            [
                {"id": uuid4(), "email": "null-one@example.com", "name": "One"},
                {"id": uuid4(), "email": "null-two@example.com", "name": "Two"},
            ],
        )


def test_tenant_object_composite_unique_and_nullable_pairs(
    integration_engine,
    clean_business_tables,
):
    del clean_business_tables
    tenant_id = uuid4()
    object_id = uuid4()
    with integration_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, tenant_id, external_object_id, email, display_name) "
                "VALUES (:id, :tenant_id, :object_id, :email, :name)"
            ),
            {
                "id": uuid4(),
                "tenant_id": tenant_id,
                "object_id": object_id,
                "email": "entra-one@example.com",
                "name": "Entra One",
            },
        )

    assert_insert_fails(
        integration_engine,
        "INSERT INTO users "
        "(id, tenant_id, external_object_id, email, display_name) "
        "VALUES (:id, :tenant_id, :object_id, :email, :name)",
        {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "object_id": object_id,
            "email": "entra-two@example.com",
            "name": "Entra Two",
        },
    )

    with integration_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, tenant_id, external_object_id, email, display_name) "
                "VALUES (:id, NULL, NULL, :email, :name)"
            ),
            [
                {"id": uuid4(), "email": "pair-null-one@example.com", "name": "One"},
                {"id": uuid4(), "email": "pair-null-two@example.com", "name": "Two"},
            ],
        )
