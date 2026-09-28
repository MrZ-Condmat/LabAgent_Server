"""Credential-safe validation for the Long-term Beta runtime environment."""

import os
from urllib.parse import urlsplit

import pytz

from .config import Config


REQUIRED_RUNTIME_FIELDS = (
    "DATABASE_URL",
    "LABAGENT_ALLOWED_EMAIL_DOMAINS",
    "AUTH_OTP_HMAC_SECRET",
    "AUTH_SESSION_HMAC_SECRET",
    "AUTH_COOKIE_NAME",
    "AUTH_COOKIE_SECURE",
    "LABAGENT_AUTH_GATEWAY_PUBLIC_URL",
    "LABAGENT_STREAMLIT_PUBLIC_URL",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "SMTP_USE_SSL",
    "DEEPSEEK_CHAT_API_KEY",
    "DEEPSEEK_SCORING_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEFAULT_TIMEZONE",
    "STREAMLIT_HOST",
    "STREAMLIT_PORT",
)

_BOOLEAN_VALUES = {"true", "false", "1", "0", "yes", "no", "on", "off"}
_PLACEHOLDERS = (
    "change_me",
    "<password>",
    "<api_key>",
    "<api_base_url>",
    "<app_password>",
    "<generate_random_secret>",
    "<school_email>",
    "server_ip",
)


def _contains_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _PLACEHOLDERS)


def _valid_port(value: str) -> bool:
    try:
        port = int(value)
    except ValueError:
        return False
    return 1 <= port <= 65535


def _valid_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _valid_database_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (
        parsed.scheme in {"postgresql", "postgresql+psycopg"}
        and bool(parsed.hostname)
        and bool(parsed.username)
        and parsed.password is not None
        and bool(parsed.path.strip("/"))
    )


def validate_long_term_beta_runtime() -> dict[str, str]:
    """Return SET/MISSING/INVALID statuses without returning configuration values."""
    statuses: dict[str, str] = {}
    values = {name: (os.getenv(name) or "").strip() for name in REQUIRED_RUNTIME_FIELDS}

    for name, value in values.items():
        if not value:
            statuses[name] = "MISSING"
        elif _contains_placeholder(value):
            statuses[name] = "INVALID"
        else:
            statuses[name] = "SET"

    for name in ("AUTH_OTP_HMAC_SECRET", "AUTH_SESSION_HMAC_SECRET"):
        if statuses[name] == "SET" and len(values[name]) < 32:
            statuses[name] = "INVALID"

    for name in ("STREAMLIT_PORT", "SMTP_PORT"):
        if statuses[name] == "SET" and not _valid_port(values[name]):
            statuses[name] = "INVALID"

    for name in ("AUTH_COOKIE_SECURE", "SMTP_USE_SSL"):
        if statuses[name] == "SET" and values[name].lower() not in _BOOLEAN_VALUES:
            statuses[name] = "INVALID"

    for name in (
        "LABAGENT_AUTH_GATEWAY_PUBLIC_URL",
        "LABAGENT_STREAMLIT_PUBLIC_URL",
        "DEEPSEEK_BASE_URL",
    ):
        if statuses[name] == "SET" and not _valid_http_url(values[name]):
            statuses[name] = "INVALID"

    if statuses["DATABASE_URL"] == "SET" and not _valid_database_url(
        values["DATABASE_URL"]
    ):
        statuses["DATABASE_URL"] = "INVALID"

    if statuses["LABAGENT_ALLOWED_EMAIL_DOMAINS"] == "SET":
        domains = [item.strip() for item in values["LABAGENT_ALLOWED_EMAIL_DOMAINS"].split(",")]
        if not domains or any(not item for item in domains):
            statuses["LABAGENT_ALLOWED_EMAIL_DOMAINS"] = "INVALID"

    if statuses["DEFAULT_TIMEZONE"] == "SET":
        try:
            pytz.timezone(values["DEFAULT_TIMEZONE"])
        except pytz.UnknownTimeZoneError:
            statuses["DEFAULT_TIMEZONE"] = "INVALID"

    if all(status == "SET" for status in statuses.values()):
        try:
            Config()
        except (TypeError, ValueError):
            statuses["CONFIG"] = "INVALID"

    return statuses
