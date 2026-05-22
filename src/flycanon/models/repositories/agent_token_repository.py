# Copyright 2026 Firefly Software Solutions Inc
"""``AgentTokenRepository`` -- async SQLAlchemy data-access for agent tokens.

Shape mirrors :class:`WorkspaceRepository` -- the repository takes a
shared ``async_sessionmaker`` (built via
:func:`flycanon.models.repositories._engine.build_session_factory` so
every flycanon repository sits on the same cached engine) plus an
optional :class:`AsyncEngine` so the actuator's database health probe
can reach the engine through the ``engine`` property. The repository
exposes coroutine methods returning plain ``dict`` rows so the service
layer never imports SQLAlchemy directly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flycanon.models.entities.agent_token import AgentToken
from flycanon.models.repositories._engine import build_session_factory


class AgentTokenRepository:
    """Async repository over the ``canon_agent_tokens`` table."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        engine: AsyncEngine | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._engine = engine

    @property
    def engine(self) -> AsyncEngine | None:
        """Underlying ``AsyncEngine`` -- consumed by the actuator probe."""
        return self._engine

    @classmethod
    def from_url(cls, database_url: str, *, echo: bool = False) -> AgentTokenRepository:
        factory, engine = build_session_factory(database_url, echo=echo)
        return cls(factory, engine=engine)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def insert(self, row: dict[str, Any]) -> None:
        async with self._session_factory() as session, session.begin():
            session.add(AgentToken(**row))

    async def revoke(self, token_id: str, *, at: datetime) -> bool:
        """Set ``revoked_at`` only if the row is not already revoked.

        Returns ``True`` when this call flipped the row, ``False`` when
        the row is missing or had already been revoked -- mirrors the
        idempotent contract the service layer documents.
        """
        async with self._session_factory() as session, session.begin():
            stmt = (
                sa_update(AgentToken)
                .where(
                    AgentToken.id == token_id,
                    AgentToken.revoked_at.is_(None),
                )
                .values(revoked_at=at)
            )
            result = await session.execute(stmt)
            rowcount = getattr(result, "rowcount", 0) or 0
            return rowcount > 0

    async def mark_used(self, token_id: str, *, at: datetime) -> None:
        async with self._session_factory() as session, session.begin():
            stmt = sa_update(AgentToken).where(AgentToken.id == token_id).values(last_used_at=at)
            await session.execute(stmt)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_by_prefix(self, prefix: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            stmt = select(AgentToken).where(
                AgentToken.prefix == prefix,
                AgentToken.revoked_at.is_(None),
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            return _row_to_dict(row) if row else None

    async def list_for_tenant(self, tenant_id: str) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            stmt = (
                select(AgentToken)
                .where(AgentToken.tenant_id == tenant_id)
                .order_by(AgentToken.created_at.desc())
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: AgentToken) -> dict[str, Any]:
    """Materialise the ORM row into the plain dict the service layer uses."""
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "name": row.name,
        "prefix": row.prefix,
        "secret_hash": row.secret_hash,
        "workspace_allowlist_json": row.workspace_allowlist_json,
        "scopes_json": row.scopes_json,
        "rate_limit_rpm": row.rate_limit_rpm,
        "expires_at": row.expires_at,
        "created_at": row.created_at,
        "created_by": row.created_by,
        "revoked_at": row.revoked_at,
        "last_used_at": row.last_used_at,
    }
