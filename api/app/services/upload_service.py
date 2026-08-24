import hashlib
import os
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.db import File, FileFormat, Job, Session, SessionStatus


ALLOWED_EXTENSIONS = {
    "csv": FileFormat.csv,
    "tsv": FileFormat.csv,
    "json": FileFormat.json,
    "sql": FileFormat.sql,
    "parquet": FileFormat.parquet,
}


def _get_format(filename: str) -> FileFormat:
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file extension: .{ext}")
    return ALLOWED_EXTENSIONS[ext]


def _validate_tier0(files: list[UploadFile]) -> None:
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    if len(files) > settings.max_files_per_session:
        raise HTTPException(status_code=400, detail=f"Max {settings.max_files_per_session} files per upload")

    filenames = [f.filename for f in files]
    if len(filenames) != len(set(filenames)):
        raise HTTPException(status_code=400, detail="Duplicate filenames in upload")

    formats = {_get_format(f.filename) for f in files}
    if len(formats) > 1:
        raise HTTPException(status_code=400, detail="Mixed file formats are not allowed")


async def _compute_sha256(file: UploadFile) -> tuple[bytes, str]:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail=f"File '{file.filename}' is empty")
    sha256 = hashlib.sha256(content).hexdigest()
    return content, sha256


async def _check_duplicate(sha256: str, db: AsyncSession) -> None:
    result = await db.execute(select(File).where(File.sha256_hash == sha256))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"File with this content already exists")


async def process_upload(files: list[UploadFile], db: AsyncSession) -> tuple[Session, Job]:
    _validate_tier0(files)

    file_format = _get_format(files[0].filename)
    max_bytes = settings.max_file_size_mb * 1024 * 1024

    file_contents: list[tuple[UploadFile, bytes, str]] = []
    total_size = 0

    for file in files:
        content, sha256 = await _compute_sha256(file)

        size = len(content)
        total_size += size

        if total_size > max_bytes:
            raise HTTPException(status_code=413, detail=f"Total file size exceeds {settings.max_file_size_mb}MB limit")

        await _check_duplicate(sha256, db)
        file_contents.append((file, content, sha256))

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

    for file, content, sha256 in file_contents:
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
