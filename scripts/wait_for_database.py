"""Wait for the configured PostgreSQL database without exposing credentials."""

import argparse
import sys
import time
from pathlib import Path
from typing import Callable, Optional, Sequence

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lab_agent.utils import Config, RuntimeConfigurationError, load_project_dotenv  # noqa: E402
from lab_agent.utils.runtime_validation import validate_long_term_beta_runtime  # noqa: E402


EngineFactory = Callable[[str], Engine]


def _create_readiness_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def wait_for_database(
    database_url: str,
    *,
    timeout_seconds: float = 60.0,
    interval_seconds: float = 2.0,
    engine_factory: EngineFactory = _create_readiness_engine,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Return when SELECT 1 succeeds or the timeout expires."""
    if timeout_seconds < 0:
        raise ValueError("timeout must be non-negative")
    if interval_seconds <= 0:
        raise ValueError("interval must be positive")

    try:
        engine = engine_factory(database_url)
    except Exception:
        print("Unable to initialize the database readiness check.", file=sys.stderr)
        return False

    deadline = monotonic() + timeout_seconds
    try:
        while True:
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                print("Database is ready.")
                return True
            except Exception:
                now = monotonic()
                if now >= deadline:
                    print(
                        "Timed out waiting for the database to become ready.",
                        file=sys.stderr,
                    )
                    return False
                sleep(min(interval_seconds, deadline - now))
    finally:
        engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=2.0)
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    engine_factory: EngineFactory = _create_readiness_engine,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    args = _parser().parse_args(argv)
    try:
        load_project_dotenv()
        statuses = validate_long_term_beta_runtime()
        if not all(status == "SET" for status in statuses.values()):
            print(
                "Runtime configuration is invalid; run validate_runtime_config.py.",
                file=sys.stderr,
            )
            return 1
        config = Config()
    except (RuntimeConfigurationError, TypeError, ValueError):
        print(
            "Runtime configuration is unavailable or invalid; run validate_runtime_config.py.",
            file=sys.stderr,
        )
        return 1

    if not config.database_url:
        print("Database configuration is missing.", file=sys.stderr)
        return 1

    try:
        ready = wait_for_database(
            config.database_url,
            timeout_seconds=args.timeout,
            interval_seconds=args.interval,
            engine_factory=engine_factory,
            monotonic=monotonic,
            sleep=sleep,
        )
    except ValueError as exc:
        print(f"Invalid readiness timing: {exc}", file=sys.stderr)
        return 2
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
