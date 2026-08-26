"""Turns an accepted upload into a Session, its File rows, and a queued Job."""

import hashlib
import logging
import uuid
from dataclasses import dataclass

from fastapi import UploadFile
from ontology_shared.models import File, FileFormat, Job, Session, SessionStatus
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.exceptions import (
    ApiError,
    DuplicateFileError,
    DuplicateFilenameError,
    EmptyFileError,
    FileTooLargeError,
    UploadConflictError,
)
from app.services.storage import FileStorage
from app.services.validation import safe_filename, validate_upload

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedFile:
    """An upload that passed validation, held in memory until the Session row
    exists to group it under."""

    filename: str
    content: bytes
    sha256: str

    @property
    def size_bytes(self) -> int:
        return len(self.content)


@dataclass(frozen=True)
class PreparedBatch:
    """The whole upload, with the total already accumulated while reading so
    it is not summed a second time."""

    files: list[PreparedFile]
    total_bytes: int


async def _read_and_hash(file: UploadFile) -> PreparedFile:
    filename = safe_filename(file.filename)
    content = await file.read()
    if not content:
        raise EmptyFileError(filename)

    return PreparedFile(
        filename=filename,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
    )


async def _reject_already_uploaded(prepared: list[PreparedFile], db: AsyncSession) -> None:
    """Reject files whose exact bytes were uploaded before, in one query
    rather than one per file."""
    by_hash = {item.sha256: item for item in prepared}

    result = await db.execute(
        select(File.sha256_hash).where(File.sha256_hash.in_(by_hash.keys()))
    )
    for known_hash in result.scalars():
        raise DuplicateFileError(by_hash[known_hash].filename)


async def _prepare_files(files: list[UploadFile], db: AsyncSession) -> PreparedBatch:
    """Read every file once, hashing and size-checking as it goes.

    The size check runs against the running total so an oversized batch is
    rejected as soon as it crosses the limit, without reading the rest.
    """
    prepared: list[PreparedFile] = []
    total_bytes = 0
    seen_hashes: dict[str, str] = {}

    for file in files:
        item = await _read_and_hash(file)

        total_bytes += item.size_bytes
        if total_bytes > settings.max_upload_bytes:
            raise FileTooLargeError(settings.max_file_size_mb)

        # Two names for identical bytes would produce redundant nodes later.
        if item.sha256 in seen_hashes:
            raise DuplicateFileError(item.filename)
        seen_hashes[item.sha256] = item.filename

        prepared.append(item)

    await _reject_already_uploaded(prepared, db)
    return PreparedBatch(files=prepared, total_bytes=total_bytes)


def _as_domain_error(error: IntegrityError, batch: PreparedBatch) -> ApiError:
    """Translate a constraint violation into the rule the user broke.

    The violated constraint is named in the driver's error, which is more
    reliable than re-querying to work out what collided.
    """
    detail = str(error.orig)

    if "uq_files_sha256_hash" in detail:
        return DuplicateFileError(batch.files[0].filename)
    if "uq_files_session_filename" in detail:
        return DuplicateFilenameError(batch.files[0].filename)

    log.error("Unmapped integrity error on upload: %s", detail)
    return UploadConflictError()


def _build_session(file_format: FileFormat, batch: PreparedBatch) -> Session:
    """Build the Session row.

    The id is generated here rather than left to the database so that storage
    can group the files under it before anything is written.
    """
    return Session(
        session_id=uuid.uuid4(),
        format=file_format,
        total_files=len(batch.files),
        total_size_bytes=batch.total_bytes,
        status=SessionStatus.queued,
    )


async def process_upload(
    files: list[UploadFile],
    db: AsyncSession,
    storage: FileStorage,
) -> tuple[Session, Job]:
    """Validate, store, and record an upload.

    Everything is committed in one transaction, so a failure part-way through
    leaves no half-recorded session behind. The job is published only after
    this returns, meaning the Worker never sees a job whose rows are missing.
    """
    file_format = validate_upload(files)
    batch = await _prepare_files(files, db)

    session = _build_session(file_format, batch)
    db.add(session)

    for item in batch.files:
        db.add(
            File(
                session_id=session.session_id,
                original_filename=item.filename,
                sha256_hash=item.sha256,
                size_bytes=item.size_bytes,
                stored_path=storage.save(session.session_id, item.filename, item.content),
            )
        )

    job = Job(job_id=uuid.uuid4(), session_id=session.session_id)
    db.add(job)

    try:
        await db.commit()
    except IntegrityError as error:
        # A concurrent upload of the same bytes can pass the pre-check above and
        # only collide here, where the unique constraint settles it.
        await db.rollback()
        raise _as_domain_error(error, batch) from error

    log.info(
        "Upload stored: session_id=%s format=%s files=%d bytes=%d",
        session.session_id,
        file_format.value,
        session.total_files,
        session.total_size_bytes,
    )
    return session, job
