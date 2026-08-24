import hashlib
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.exceptions import (
    DuplicateFileError,
    DuplicateFilenameError,
    EmptyFileError,
    FileTooLargeError,
    InvalidFileExtensionError,
    MixedFormatsError,
    TooManyFilesError,
)
from app.models.db import File, FileFormat, Job, Session, SessionStatus

ALLOWED_EXTENSIONS: dict[str, FileFormat] = {
    "csv": FileFormat.csv,
    "tsv": FileFormat.csv,
    "json": FileFormat.json,
    "sql": FileFormat.sql,
    "parquet": FileFormat.parquet,
}

MAX_BYTES = settings.max_file_size_mb * 1024 * 1024


def get_format(filename: str) -> FileFormat:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise InvalidFileExtensionError(ext)
    return ALLOWED_EXTENSIONS[ext]


def validate_tier0(files: list[UploadFile]) -> None:
    if len(files) > settings.max_files_per_session:
        raise TooManyFilesError(settings.max_files_per_session)

    filenames = [f.filename for f in files]
    if len(filenames) != len(set(filenames)):
        raise DuplicateFilenameError()

    formats = {get_format(f.filename) for f in files}
    if len(formats) > 1:
        raise MixedFormatsError()


async def read_file_content(file: UploadFile) -> tuple[bytes, str]:
    content = await file.read()
    if not content:
        raise EmptyFileError(file.filename)
    sha256 = hashlib.sha256(content).hexdigest()
    return content, sha256


async def check_duplicate(sha256: str, db: AsyncSession) -> None:
    result = await db.execute(select(File).where(File.sha256_hash == sha256))
    if result.scalar_one_or_none() is not None:
        raise DuplicateFileError()


async def process_upload(files: list[UploadFile], db: AsyncSession) -> tuple[Session, Job]:
    validate_tier0(files)

    file_format = get_format(files[0].filename)
    collected: list[tuple[UploadFile, bytes, str]] = []
    total_size = 0

    for file in files:
        content, sha256 = await read_file_content(file)
        total_size += len(content)

        if total_size > MAX_BYTES:
            raise FileTooLargeError(settings.max_file_size_mb)

        await check_duplicate(sha256, db)
        collected.append((file, content, sha256))

    session = Session(
        format=file_format,
        total_files=len(files),
        total_size_bytes=total_size,
        status=SessionStatus.queued,
    )
    db.add(session)
    await db.flush()

    upload_dir = Path(settings.upload_dir) / str(session.session_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    for file, content, sha256 in collected:
        stored_path = upload_dir / file.filename
        stored_path.write_bytes(content)
        db.add(File(
            session_id=session.session_id,
            original_filename=file.filename,
            sha256_hash=sha256,
            size_bytes=len(content),
            stored_path=str(stored_path),
        ))

    job = Job(session_id=session.session_id)
    db.add(job)

    await db.commit()
    await db.refresh(session)
    await db.refresh(job)

    return session, job
