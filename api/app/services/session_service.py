"""Operations on an upload once it has been recorded."""

import logging
import uuid

from ontology_shared.models import Session, SessionStatus
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import SessionInUseError, SessionNotFoundError
from app.services.storage import FileStorage

log = logging.getLogger(__name__)

#: A session may only be removed once nothing is working on it. Deleting one
#: mid-parse would pull the files out from under the Worker, which would then
#: retry a session that no longer exists.
DELETABLE_STATUSES = frozenset({SessionStatus.ready, SessionStatus.failed})


async def delete_session(session_id: uuid.UUID, db: AsyncSession, storage: FileStorage) -> None:
    """Remove a session, its records, and its stored files.

    Files go first and the database record last. The record is what makes the
    files findable, so keeping it until they are gone means an interrupted
    delete leaves something that can be retried, rather than files that
    nothing points to. Storage deletion tolerates already-missing files, so
    retrying is safe.

    The File and Job rows are removed by the foreign keys' cascade.
    """
    session = await db.get(Session, session_id)
    if session is None:
        raise SessionNotFoundError(str(session_id))

    if session.status not in DELETABLE_STATUSES:
        raise SessionInUseError(str(session_id), session.status.value)

    storage.delete(session_id)
    await db.delete(session)
    await db.commit()

    log.info("Session deleted: session_id=%s", session_id)
