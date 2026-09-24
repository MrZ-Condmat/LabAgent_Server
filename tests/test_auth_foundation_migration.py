from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_revision_and_offline_sql(monkeypatch):
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0004_email_otp_auth_foundation.py"
    spec = spec_from_file_location("otp_migration", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0004_email_otp_auth_foundation"
    assert module.down_revision == "0003_entra_identity"
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:test@localhost/testdb")
    config = Config("alembic.ini")
    config.output_buffer = StringIO()
    command.upgrade(config, "head", sql=True)
    sql = config.output_buffer.getvalue().lower()
    assert "create table email_login_challenges" in sql
    assert "create table user_sessions" in sql
    assert "for update" not in sql
