"""Shared request dependencies."""

from functools import lru_cache

from ontology_shared.storage import FileStorage, LocalFileStorage

from app.core.config import settings


@lru_cache(maxsize=1)
def get_storage() -> FileStorage:
    """The configured file store.

    Injected rather than imported so tests can substitute an in-memory store,
    and so a future object-store implementation is a one-line change here.
    """
    return LocalFileStorage(settings.upload_dir)
