import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import UploadFile
from httpx import AsyncClient

from app.core.database import get_db
from app.exceptions import DuplicateFileError
from app.main import app
from app.models.db import FileFormat, JobStatus, SessionStatus
from app.services.upload_service import get_format, validate_tier0


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_upload_file(filename: str) -> MagicMock:
    f = MagicMock(spec=UploadFile)
    f.filename = filename
    return f


def csv_file(name: str = "data.csv", content: bytes = b"col1,col2\nval1,val2") -> tuple:
    return ("files", (name, content, "text/csv"))


# ── Router: POST /api/upload ──────────────────────────────────────────────────

class TestUploadEndpoint:
    async def test_upload_success_returns_201(
        self,
        client: AsyncClient,
        mock_session: MagicMock,
        mock_job: MagicMock,
    ) -> None:
        with (
            patch("app.routers.upload.process_upload", new_callable=AsyncMock, return_value=(mock_session, mock_job)),
            patch("app.routers.upload.publish_job", new_callable=AsyncMock),
        ):
            response = await client.post("/api/upload", files=[csv_file()])

        assert response.status_code == 201
        body = response.json()
        assert body["session_id"] == str(mock_session.session_id)
        assert body["job_id"] == str(mock_job.job_id)
        assert body["status"] == SessionStatus.queued
        assert body["total_files"] == 1
        assert body["total_size_bytes"] == 17

    async def test_upload_invalid_extension_returns_400(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/upload",
            files=[("files", ("data.txt", b"content", "text/plain"))],
        )
        assert response.status_code == 400
        assert "txt" in response.json()["detail"]

    async def test_upload_empty_file_returns_400(self, client: AsyncClient) -> None:
        mock_db = AsyncMock()
        mock_db.execute.return_value.scalar_one_or_none.return_value = None
        app.dependency_overrides[get_db] = lambda: mock_db

        try:
            response = await client.post("/api/upload", files=[csv_file(content=b"")])
            assert response.status_code == 400
            assert "empty" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()

    async def test_upload_mixed_formats_returns_400(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/upload",
            files=[
                csv_file("table.csv"),
                ("files", ("data.json", b'[{"a": 1}]', "application/json")),
            ],
        )
        assert response.status_code == 400
        assert "Mixed" in response.json()["detail"]

    async def test_upload_duplicate_filenames_returns_400(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/upload",
            files=[csv_file("data.csv"), csv_file("data.csv", b"col\nval2")],
        )
        assert response.status_code == 400
        assert "Duplicate" in response.json()["detail"]

    async def test_upload_too_many_files_returns_400(self, client: AsyncClient) -> None:
        files = [csv_file(f"data{i}.csv", f"col\n{i}".encode()) for i in range(6)]
        response = await client.post("/api/upload", files=files)
        assert response.status_code == 400
        assert "Max" in response.json()["detail"]

    async def test_upload_duplicate_sha256_returns_409(self, client: AsyncClient) -> None:
        with patch(
            "app.services.upload_service.check_duplicate",
            new_callable=AsyncMock,
            side_effect=DuplicateFileError(),
        ):
            mock_db = AsyncMock()
            app.dependency_overrides[get_db] = lambda: mock_db
            try:
                response = await client.post("/api/upload", files=[csv_file()])
                assert response.status_code == 409
                assert "already exists" in response.json()["detail"]
            finally:
                app.dependency_overrides.clear()

    async def test_upload_file_too_large_returns_413(self, client: AsyncClient) -> None:
        large_content = b"x" * (21 * 1024 * 1024)
        response = await client.post("/api/upload", files=[csv_file(content=large_content)])
        assert response.status_code == 413
        assert "20MB" in response.json()["detail"]


# ── Router: GET /api/sessions/{session_id} ────────────────────────────────────

class TestSessionStatusEndpoint:
    async def test_get_status_found(
        self,
        client: AsyncClient,
        mock_session: MagicMock,
        mock_job: MagicMock,
        session_id: uuid.UUID,
    ) -> None:
        mock_db = AsyncMock()
        mock_db.get.return_value = mock_session
        mock_db.execute.return_value.scalar_one_or_none.return_value = mock_job
        app.dependency_overrides[get_db] = lambda: mock_db

        try:
            response = await client.get(f"/api/sessions/{session_id}")
            assert response.status_code == 200
            body = response.json()
            assert body["session_id"] == str(session_id)
            assert body["status"] == SessionStatus.queued
            assert body["job_status"] == JobStatus.queued
            assert body["error_message"] is None
        finally:
            app.dependency_overrides.clear()

    async def test_get_status_not_found_returns_404(
        self,
        client: AsyncClient,
        session_id: uuid.UUID,
    ) -> None:
        mock_db = AsyncMock()
        mock_db.get.return_value = None
        app.dependency_overrides[get_db] = lambda: mock_db

        try:
            response = await client.get(f"/api/sessions/{session_id}")
            assert response.status_code == 404
            assert str(session_id) in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()

    async def test_get_status_invalid_uuid_returns_422(self, client: AsyncClient) -> None:
        response = await client.get("/api/sessions/not-a-uuid")
        assert response.status_code == 422


# ── Service: validate_tier0 ───────────────────────────────────────────────────

class TestValidateTier0:
    def test_too_many_files_raises(self) -> None:
        files = [make_upload_file(f"data{i}.csv") for i in range(6)]
        with pytest.raises(Exception) as exc_info:
            validate_tier0(files)
        assert exc_info.value.status_code == 400
        assert "Max" in exc_info.value.detail

    def test_duplicate_filenames_raises(self) -> None:
        files = [make_upload_file("data.csv"), make_upload_file("data.csv")]
        with pytest.raises(Exception) as exc_info:
            validate_tier0(files)
        assert exc_info.value.status_code == 400
        assert "Duplicate" in exc_info.value.detail

    def test_mixed_formats_raises(self) -> None:
        files = [make_upload_file("data.csv"), make_upload_file("data.json")]
        with pytest.raises(Exception) as exc_info:
            validate_tier0(files)
        assert exc_info.value.status_code == 400
        assert "Mixed" in exc_info.value.detail

    def test_valid_single_file_passes(self) -> None:
        validate_tier0([make_upload_file("data.csv")])

    def test_valid_multiple_same_format_passes(self) -> None:
        files = [make_upload_file("table1.csv"), make_upload_file("table2.csv")]
        validate_tier0(files)


# ── Service: get_format ───────────────────────────────────────────────────────

class TestGetFormat:
    @pytest.mark.parametrize("filename,expected", [
        ("data.csv", FileFormat.csv),
        ("data.tsv", FileFormat.csv),
        ("data.CSV", FileFormat.csv),
        ("data.json", FileFormat.json),
        ("dump.sql", FileFormat.sql),
        ("data.parquet", FileFormat.parquet),
    ])
    def test_valid_extensions(self, filename: str, expected: FileFormat) -> None:
        assert get_format(filename) == expected

    @pytest.mark.parametrize("filename", ["data.txt", "data.xlsx", "data", "data.pdf"])
    def test_invalid_extension_raises(self, filename: str) -> None:
        with pytest.raises(Exception) as exc_info:
            get_format(filename)
        assert exc_info.value.status_code == 400
