"""Where a session's files are kept.

Both services depend on the ``FileStorage`` interface rather than on the
filesystem: the API writes uploads, the Worker writes parsed tables beside
them, and the API reads those back for callers.

Naming is separated from reading and writing. ``location`` decides where
something belongs and is the string recorded in the database; the reader and
writer work from that string. An object store implementation then only has to
return keys from ``location`` and open streams for them.
"""

import shutil
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


class FileStorage(ABC):
    @abstractmethod
    def location(self, session_id: uuid.UUID, *parts: str) -> str:
        """Where something belonging to this session is kept.

        ``parts`` are path segments supplied by the service, never by a
        caller, and the result is what gets recorded in the database.
        """

    @abstractmethod
    def writer(self, location: str) -> Iterator[BinaryIO]:
        """Open ``location`` for writing, creating anything it needs."""

    @abstractmethod
    def reader(self, location: str) -> Iterator[BinaryIO]:
        """Open ``location`` for reading.

        The stream supports seeking, which is what lets a reader take only the
        part of a Parquet file it needs instead of the whole of it.
        """

    @abstractmethod
    def delete(self, session_id: uuid.UUID) -> None:
        """Remove everything stored for a session.

        Deleting what is already gone must succeed, so that a caller whose
        first attempt failed part-way through can simply try again.
        """

    def save(self, session_id: uuid.UUID, filename: str, content: bytes) -> str:
        """Store ``content`` and return where it went.

        ``filename`` must already be sanitised by the caller.
        """
        location = self.location(session_id, filename)
        with self.writer(location) as stream:
            stream.write(content)
        return location


class LocalFileStorage(FileStorage):
    """Stores each session's files under ``<root>/<session_id>/``.

    Grouping by session keeps names from colliding across uploads, and makes
    everything belonging to a session — uploads and parsed tables alike —
    removable as a unit.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def location(self, session_id: uuid.UUID, *parts: str) -> str:
        return str(self._root.joinpath(str(session_id), *parts))

    @contextmanager
    def writer(self, location: str) -> Iterator[BinaryIO]:
        path = Path(location)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as stream:
            yield stream

    @contextmanager
    def reader(self, location: str) -> Iterator[BinaryIO]:
        with Path(location).open("rb") as stream:
            yield stream

    def delete(self, session_id: uuid.UUID) -> None:
        # The directory is named by a UUID the service generated, so it cannot
        # be steered outside the root by anything a caller supplied.
        shutil.rmtree(self._root / str(session_id), ignore_errors=True)
