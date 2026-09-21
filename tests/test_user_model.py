import uuid

from sqlalchemy import CheckConstraint, Enum, UniqueConstraint, Uuid

from lab_agent.db.base import Base
from lab_agent.db.models import User, UserRole


def test_user_model_registers_expected_table_and_columns():
    table = User.__table__

    assert Base.metadata.tables["users"] is table
    assert list(table.columns) == [
        table.c.id,
        table.c.external_subject,
        table.c.email,
        table.c.display_name,
        table.c.role,
        table.c.is_active,
        table.c.created_at,
        table.c.updated_at,
        table.c.last_login_at,
    ]
    assert isinstance(table.c.id.type, Uuid)
    assert table.c.id.type.as_uuid is True
    assert table.c.id.nullable is False
    assert isinstance(table.c.id.default.arg(None), uuid.UUID)
    assert table.c.external_subject.nullable is True
    assert table.c.external_subject.type.length == 255
    assert table.c.email.nullable is False
    assert table.c.email.type.length == 320
    assert table.c.display_name.nullable is False
    assert table.c.display_name.type.length == 255
    assert table.c.last_login_at.nullable is True


def test_user_model_enforces_identity_and_role_constraints():
    table = User.__table__
    unique_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert unique_names == {"uq_users_email", "uq_users_external_subject"}
    assert checks == {"ck_users_role": "role IN ('admin', 'user')"}
    assert isinstance(table.c.role.type, Enum)
    assert table.c.role.type.native_enum is False
    assert table.c.role.type.enums == ["admin", "user"]
    assert table.c.role.default.arg is UserRole.USER
    assert str(table.c.role.server_default.arg) == "user"
    assert table.c.is_active.default.arg is True
    assert str(table.c.is_active.server_default.arg).lower() == "true"


def test_user_timestamps_are_timezone_aware_and_have_defaults():
    table = User.__table__

    for name in ("created_at", "updated_at", "last_login_at"):
        assert table.c[name].type.timezone is True

    assert table.c.created_at.nullable is False
    assert table.c.created_at.default is not None
    assert table.c.created_at.server_default is not None
    assert table.c.updated_at.nullable is False
    assert table.c.updated_at.default is not None
    assert table.c.updated_at.onupdate is not None
    assert table.c.updated_at.server_default is not None
    assert table.c.last_login_at.default is None
