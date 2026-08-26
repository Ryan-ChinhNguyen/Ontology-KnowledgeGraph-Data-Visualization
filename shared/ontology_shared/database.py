from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def build_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Create the async engine.

    ``pool_pre_ping`` costs one cheap round-trip per checkout but avoids
    handing out connections that the database has already dropped — the usual
    cause of spurious errors after an idle period or a database restart.
    """
    return create_async_engine(database_url, echo=echo, pool_pre_ping=True)


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """``expire_on_commit=False`` so ORM objects stay readable after commit,
    which callers rely on when returning them from a request handler."""
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
