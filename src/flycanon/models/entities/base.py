# Copyright 2026 Firefly Software Solutions Inc
"""Declarative :class:`Base` shared by every flycanon entity.

A single ``Base`` keeps the SQLAlchemy metadata graph coherent so
Alembic's autogeneration sees every table at once. All tables in this
service are prefixed with ``canon_`` so the schema is auditable in a
multi-service Postgres instance.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base shared by every entity in this service."""
