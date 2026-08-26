import hashlib
import io
from unittest.mock import AsyncMock

import pytest
from fastapi import UploadFile
from ontology_shared.models import FileFormat, SessionStatus

from sqlalchemy.exc import IntegrityError

from app.exceptions import (
    DuplicateFileError,
    DuplicateFilenameError,
    EmptyFileError,
    FileTooLargeError,
    UploadConflictError,
)
from app.services.upload_service import process_upload
from tests.conftest import InMemoryStorage


def upload(filename: str, content: bytes = b"id,name\n1,alice\n") -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(content))


class TestProcessUpload:
    async def test_records_session_files_and_job(
        self, db: AsyncMock, storage: InMemoryStorage
    ) -> None:
        session, job = await process_upload([upload("people.csv")], db, storage)

        assert session.format is FileFormat.csv
        assert session.status is SessionStatus.queued
        assert session.total_files == 1
        assert session.total_size_bytes == len(b"id,name\n1,alice\n")
        assert job.session_id == session.session_id
        db.commit.assert_awaited_once()

    async def test_stores_each_file_under_its_session(
        self, db: AsyncMock, storage: InMemoryStorage
    ) -> None:
        session, _ = await process_upload(
            [upload("a.csv", b"a\n1\n"), upload("b.csv", b"b\n2\n")], db, storage
        )

        assert set(storage.saved) == {
            f"memory://{session.session_id}/a.csv",
            f"memory://{session.session_id}/b.csv",
        }

    async def test_sums_sizes_across_files(self, db: AsyncMock, storage: InMemoryStorage) -> None:
        session, _ = await process_upload(
            [upload("a.csv", b"1234"), upload("b.csv", b"567")], db, storage
        )
        assert session.total_size_bytes == 7

    async def test_sanitises_the_stored_filename(
        self, db: AsyncMock, storage: InMemoryStorage
    ) -> None:
        session, _ = await process_upload([upload("../../escape.csv")], db, storage)
        assert f"memory://{session.session_id}/escape.csv" in storage.saved

    async def test_rejects_empty_file(self, db: AsyncMock, storage: InMemoryStorage) -> None:
        with pytest.raises(EmptyFileError):
            await process_upload([upload("empty.csv", b"")], db, storage)

    async def test_rejects_batch_over_the_size_limit(
        self, db: AsyncMock, storage: InMemoryStorage
    ) -> None:
        oversized = b"x" * (21 * 1024 * 1024)
        with pytest.raises(FileTooLargeError):
            await process_upload([upload("big.csv", oversized)], db, storage)

    async def test_rejects_identical_content_under_two_names(
        self, db: AsyncMock, storage: InMemoryStorage
    ) -> None:
        with pytest.raises(DuplicateFileError):
            await process_upload(
                [upload("first.csv", b"same"), upload("second.csv", b"same")], db, storage
            )

    async def test_checks_all_hashes_in_a_single_query(
        self, db: AsyncMock, storage: InMemoryStorage
    ) -> None:
        """Guards against the duplicate check drifting back inside the loop."""
        batch = [upload(f"table{index}.csv", f"id\n{index}\n".encode()) for index in range(5)]

        await process_upload(batch, db, storage)

        assert db.execute.await_count == 1

    async def test_rejects_content_uploaded_previously(
        self, db: AsyncMock, storage: InMemoryStorage
    ) -> None:
        content = b"id\n1\n"
        db.execute.return_value.scalars.return_value = [hashlib.sha256(content).hexdigest()]

        with pytest.raises(DuplicateFileError):
            await process_upload([upload("again.csv", content)], db, storage)

    async def test_reports_a_concurrent_duplicate_as_a_conflict(
        self, db: AsyncMock, storage: InMemoryStorage
    ) -> None:
        """Two uploads of the same bytes can both pass the pre-check; the one
        that loses the race must still get a clear error, not a 500."""
        db.commit.side_effect = IntegrityError(
            statement="INSERT INTO files ...",
            params={},
            orig=Exception('duplicate key value violates unique constraint "uq_files_sha256_hash"'),
        )

        with pytest.raises(DuplicateFileError):
            await process_upload([upload("people.csv")], db, storage)

        db.rollback.assert_awaited_once()

    async def test_reports_a_filename_collision_as_a_conflict(
        self, db: AsyncMock, storage: InMemoryStorage
    ) -> None:
        db.commit.side_effect = IntegrityError(
            statement="INSERT INTO files ...",
            params={},
            orig=Exception(
                'duplicate key value violates unique constraint "uq_files_session_filename"'
            ),
        )

        with pytest.raises(DuplicateFilenameError):
            await process_upload([upload("people.csv")], db, storage)

    async def test_reports_an_unrecognised_constraint_as_a_conflict(
        self, db: AsyncMock, storage: InMemoryStorage
    ) -> None:
        db.commit.side_effect = IntegrityError(
            statement="INSERT INTO sessions ...",
            params={},
            orig=Exception('violates check constraint "ck_sessions_total_files_positive"'),
        )

        with pytest.raises(UploadConflictError):
            await process_upload([upload("people.csv")], db, storage)

    async def test_writes_nothing_when_validation_fails(
        self, db: AsyncMock, storage: InMemoryStorage
    ) -> None:
        with pytest.raises(EmptyFileError):
            await process_upload([upload("ok.csv", b"a\n1\n"), upload("bad.csv", b"")], db, storage)

        assert storage.saved == {}
        db.commit.assert_not_awaited()
