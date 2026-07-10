import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Union

import pytz
from dotenv import load_dotenv


SERVER_PROJECT_ROOT = Path("/data/zmr/projects/labAgent_Server")


def local_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def project_root() -> Path:
    configured = os.getenv("LABAGENT_PROJECT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()

    if SERVER_PROJECT_ROOT.exists() or os.name != "nt":
        return SERVER_PROJECT_ROOT

    return local_project_root()


def project_path(path: Union[str, os.PathLike]) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    return project_root() / candidate


def project_path_str(path: Union[str, os.PathLike]) -> str:
    return str(project_path(path))


def load_project_dotenv(config_path: Optional[Union[str, os.PathLike]] = None) -> None:
    env_path = Path(config_path).expanduser() if config_path else project_path(".env")
    load_dotenv(env_path)


def timezone_name() -> str:
    return os.getenv("DEFAULT_TIMEZONE", "Asia/Shanghai")


def app_timezone():
    try:
        return pytz.timezone(timezone_name())
    except pytz.UnknownTimeZoneError:
        return pytz.timezone("Asia/Shanghai")


def configure_process_timezone() -> None:
    os.environ["TZ"] = timezone_name()
    if hasattr(time, "tzset"):
        time.tzset()


def now() -> datetime:
    return datetime.now(app_timezone())


def today_str() -> str:
    return now().strftime("%Y-%m-%d")


def yesterday_str() -> str:
    return (now() - timedelta(days=1)).strftime("%Y-%m-%d")


def timestamp_str() -> str:
    return now().strftime("%Y-%m-%d %H:%M:%S")


def to_app_timezone(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = pytz.utc.localize(value)
    return value.astimezone(app_timezone())
