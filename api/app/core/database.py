from collections.abc import AsyncIterator

from ontology_shared.database import build_engine, build_session_factory
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

engine = build_engine(settings.database_url)
session_factory = build_session_factory(engine)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Request-scoped database session.

    The session is closed when the request ends. Handlers commit explicitly;
    an unhandled exception leaves the transaction uncommitted.
    """
    async with session_factory() as session:
        yield session


async def dispose_engine() -> None:
    await engine.dispose()
