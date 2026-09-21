"""Shared SQLAlchemy declarative base for future ORM models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all future LabAgent ORM models."""
