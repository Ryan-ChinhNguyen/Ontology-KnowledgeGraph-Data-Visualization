from ontology_shared.database import build_engine, build_session_factory

from app.core.config import settings

engine = build_engine(settings.database_url)
session_factory = build_session_factory(engine)


async def dispose_engine() -> None:
    await engine.dispose()
