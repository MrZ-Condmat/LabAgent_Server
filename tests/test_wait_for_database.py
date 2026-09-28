"""Offline tests for the credential-safe database readiness helper."""

from types import SimpleNamespace

import pytest

from scripts import wait_for_database as waiter


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeConnection:
    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement):
        self.engine.queries.append(str(statement))


class FakeEngine:
    def __init__(self, failures: int = 0):
        self.failures = failures
        self.attempts = 0
        self.queries: list[str] = []
        self.disposed = False

    def connect(self):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise OSError("database unavailable")
        return FakeConnection(self)

    def dispose(self):
        self.disposed = True


def test_database_immediately_ready_returns_success(capsys):
    engine = FakeEngine()

    ready = waiter.wait_for_database(
        "postgresql+psycopg://fake:secret@127.0.0.1/fake",
        engine_factory=lambda _url: engine,
    )

    assert ready is True
    assert engine.attempts == 1
    assert engine.queries == ["SELECT 1"]
    assert engine.disposed is True
    assert "Database is ready" in capsys.readouterr().out


def test_database_unavailable_then_ready_retries():
    engine = FakeEngine(failures=2)
    clock = FakeClock()

    ready = waiter.wait_for_database(
        "postgresql+psycopg://fake:secret@127.0.0.1/fake",
        timeout_seconds=10,
        interval_seconds=2,
        engine_factory=lambda _url: engine,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert ready is True
    assert engine.attempts == 3
    assert clock.sleeps == [2, 2]
    assert engine.disposed is True


def test_database_timeout_is_nonzero_and_does_not_expose_url(capsys):
    engine = FakeEngine(failures=100)
    clock = FakeClock()
    database_url = "postgresql+psycopg://fake:unique-password@127.0.0.1/fake"

    ready = waiter.wait_for_database(
        database_url,
        timeout_seconds=3,
        interval_seconds=2,
        engine_factory=lambda _url: engine,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    captured = capsys.readouterr()
    assert ready is False
    assert engine.attempts == 3
    assert clock.sleeps == [2, 1]
    assert engine.disposed is True
    assert database_url not in captured.out + captured.err
    assert "unique-password" not in captured.out + captured.err


def test_engine_initialization_failure_does_not_expose_exception(capsys):
    database_url = "postgresql+psycopg://fake:another-secret@127.0.0.1/fake"

    def failing_factory(_url):
        raise RuntimeError(f"driver rejected {_url}")

    assert waiter.wait_for_database(database_url, engine_factory=failing_factory) is False
    output = capsys.readouterr()
    assert "another-secret" not in output.out + output.err
    assert database_url not in output.out + output.err
    assert "Unable to initialize" in output.err


def test_main_rejects_invalid_runtime_before_connecting(monkeypatch, capsys):
    monkeypatch.setattr(waiter, "load_project_dotenv", lambda: None)
    monkeypatch.setattr(
        waiter,
        "validate_long_term_beta_runtime",
        lambda: {"DATABASE_URL": "INVALID"},
    )
    connected = False

    def engine_factory(_url):
        nonlocal connected
        connected = True
        return FakeEngine()

    assert waiter.main([], engine_factory=engine_factory) == 1
    assert connected is False
    assert "invalid" in capsys.readouterr().err.lower()


def test_main_uses_configurable_timing_and_database_url(monkeypatch):
    database_url = "postgresql+psycopg://fake:private@127.0.0.1/fake"
    monkeypatch.setattr(waiter, "load_project_dotenv", lambda: None)
    monkeypatch.setattr(
        waiter,
        "validate_long_term_beta_runtime",
        lambda: {"DATABASE_URL": "SET"},
    )
    monkeypatch.setattr(waiter, "Config", lambda: SimpleNamespace(database_url=database_url))
    engine = FakeEngine(failures=1)
    clock = FakeClock()
    received: list[str] = []

    assert waiter.main(
        ["--timeout", "4", "--interval", "1.5"],
        engine_factory=lambda url: received.append(url) or engine,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    ) == 0
    assert received == [database_url]
    assert clock.sleeps == [1.5]


def test_missing_external_runtime_file_exits_nonzero(monkeypatch, tmp_path, capsys):
    missing = tmp_path / "missing.env"
    monkeypatch.setenv("LABAGENT_ENV_FILE", str(missing))

    assert waiter.main(["--timeout", "0"]) == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "configuration is unavailable" in output
    assert "password" not in output.lower()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--timeout", "-1"], "non-negative"),
        (["--interval", "0"], "positive"),
    ],
)
def test_invalid_timing_is_rejected(monkeypatch, arguments, message, capsys):
    monkeypatch.setattr(waiter, "load_project_dotenv", lambda: None)
    monkeypatch.setattr(
        waiter,
        "validate_long_term_beta_runtime",
        lambda: {"DATABASE_URL": "SET"},
    )
    monkeypatch.setattr(
        waiter,
        "Config",
        lambda: SimpleNamespace(database_url="postgresql+psycopg://fake:secret@db/fake"),
    )

    assert waiter.main(arguments, engine_factory=lambda _url: FakeEngine()) == 2
    assert message in capsys.readouterr().err
