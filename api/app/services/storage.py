"""Where uploaded bytes are kept.

Callers depend on the ``FileStorage`` interface rather than on the filesystem,
so moving to S3 or Azure Blob later means adding an implementation here and
changing nothing else.
"""

import uuid
from abc import ABC, abstractmethod
from pathlib import Path


class FileStorage(ABC):
    @abstractmethod
    def save(self, session_id: uuid.UUID, filename: str, content: bytes) -> str:
        """Persist ``content`` and return the location recorded on the File row.

        ``filename`` must already be sanitised by the caller.
        """


class LocalFileStorage(FileStorage):
    """Stores each session's files under ``<root>/<session_id>/``.

    Grouping by session keeps names from colliding across uploads and makes a
    session's files removable as a unit.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def save(self, session_id: uuid.UUID, filename: str, content: bytes) -> str:
        directory = self._root / str(session_id)
        directory.mkdir(parents=True, exist_ok=True)

        destination = directory / filename
        destination.write_bytes(content)
        return str(destination)
