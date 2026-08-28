import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient
from ontology_shared.models import JobStatus, SessionStatus

CSV_BODY = b"id,name\n1,alice\n"


def csv_part(name: str = "people.csv", content: bytes = CSV_BODY) -> tuple:
    return ("files", (name, content, "text/csv"))


class TestUploadEndpoint:
    async def test_accepts_a_valid_upload(self, client: AsyncClient, publisher: AsyncMock) -> None:
        response = await client.post("/api/upload", files=[csv_part()])

        assert response.status_code == 201
        body = response.json()
        assert uuid.UUID(body["session_id"])
        assert uuid.UUID(body["job_id"])
        assert body["status"] == SessionStatus.queued
        assert body["total_files"] == 1
        assert body["total_size_bytes"] == len(CSV_BODY)

    async def test_publishes_the_job_after_storing(
        self, client: AsyncClient, publisher: AsyncMock
    ) -> None:
        response = await client.post("/api/upload", files=[csv_part()])

        body = response.json()
        publisher.assert_awaited_once()
        published_job, published_session = publisher.await_args.args
        assert str(published_job) == body["job_id"]
        assert str(published_session) == body["session_id"]

    async def test_rejects_unsupported_extension(
        self, client: AsyncClient, publisher: AsyncMock
    ) -> None:
        response = await client.post(
            "/api/upload", files=[("files", ("notes.txt", b"hello", "text/plain"))]
        )
        assert response.status_code == 400
        assert "txt" in response.json()["detail"]
        publisher.assert_not_awaited()

    async def test_rejects_empty_file(self, client: AsyncClient, publisher: AsyncMock) -> None:
        response = await client.post("/api/upload", files=[csv_part(content=b"")])
        assert response.status_code == 400
        assert "empty" in response.json()["detail"]

    async def test_rejects_mixed_formats(self, client: AsyncClient, publisher: AsyncMock) -> None:
        response = await client.post(
            "/api/upload",
            files=[csv_part(), ("files", ("records.json", b'[{"a":1}]', "application/json"))],
        )
        assert response.status_code == 400
        assert "same format" in response.json()["detail"]

    async def test_rejects_duplicate_filenames(
        self, client: AsyncClient, publisher: AsyncMock
    ) -> None:
        response = await client.post(
            "/api/upload", files=[csv_part("data.csv"), csv_part("data.csv", b"id\n2\n")]
        )
        assert response.status_code == 400
        assert "Duplicate filename" in response.json()["detail"]

    async def test_rejects_more_than_five_files(
        self, client: AsyncClient, publisher: AsyncMock
    ) -> None:
        parts = [csv_part(f"table{index}.csv", f"id\n{index}\n".encode()) for index in range(6)]
        response = await client.post("/api/upload", files=parts)
        assert response.status_code == 400
        assert "At most 5" in response.json()["detail"]

    async def test_rejects_upload_over_the_size_limit(
        self, client: AsyncClient, publisher: AsyncMock
    ) -> None:
        response = await client.post(
            "/api/upload", files=[csv_part(content=b"x" * (21 * 1024 * 1024))]
        )
        assert response.status_code == 413
        assert "20MB" in response.json()["detail"]

    async def test_rejects_previously_uploaded_content(
        self, client: AsyncClient, db: AsyncMock, publisher: AsyncMock
    ) -> None:
        import hashlib

        db.execute.return_value.scalars.return_value = [hashlib.sha256(CSV_BODY).hexdigest()]

        response = await client.post("/api/upload", files=[csv_part()])
        assert response.status_code == 409
        assert "already been uploaded" in response.json()["detail"]

    async def test_rejects_a_request_with_no_files(self, client: AsyncClient) -> None:
        response = await client.post("/api/upload", files=[])
        assert response.status_code == 422


class TestSessionStatusEndpoint:
    async def test_returns_session_and_latest_job(
        self,
        client: AsyncClient,
        db: AsyncMock,
        stored_session: MagicMock,
        stored_job: MagicMock,
    ) -> None:
        db.get.return_value = stored_session
        db.execute.return_value.scalar_one_or_none.return_value = stored_job

        response = await client.get(f"/api/sessions/{stored_session.session_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["session_id"] == str(stored_session.session_id)
        assert body["status"] == SessionStatus.ready
        assert body["job_status"] == JobStatus.done
        assert body["total_files"] == 2

    async def test_reports_the_job_error(
        self,
        client: AsyncClient,
        db: AsyncMock,
        stored_session: MagicMock,
        stored_job: MagicMock,
    ) -> None:
        stored_session.status = SessionStatus.failed
        stored_job.status = JobStatus.failed
        stored_job.error_message = "column count mismatch on row 42"
        db.get.return_value = stored_session
        db.execute.return_value.scalar_one_or_none.return_value = stored_job

        response = await client.get(f"/api/sessions/{stored_session.session_id}")

        assert response.json()["error_message"] == "column count mismatch on row 42"

    async def test_handles_a_session_with_no_job_yet(
        self, client: AsyncClient, db: AsyncMock, stored_session: MagicMock
    ) -> None:
        db.get.return_value = stored_session
        db.execute.return_value.scalar_one_or_none.return_value = None

        response = await client.get(f"/api/sessions/{stored_session.session_id}")

        assert response.status_code == 200
        assert response.json()["job_status"] is None

    async def test_returns_404_for_unknown_session(
        self, client: AsyncClient, db: AsyncMock
    ) -> None:
        db.get.return_value = None
        unknown = uuid.uuid4()

        response = await client.get(f"/api/sessions/{unknown}")

        assert response.status_code == 404
        assert str(unknown) in response.json()["detail"]

    async def test_rejects_a_malformed_session_id(self, client: AsyncClient) -> None:
        response = await client.get("/api/sessions/not-a-uuid")
        assert response.status_code == 422


class TestHealthEndpoints:
    async def test_liveness_ignores_dependencies(self, client: AsyncClient) -> None:
        """Liveness must stay green while dependencies are down, or an outage
        would get the process restarted instead of waited out."""
        with (
            patch("app.main.broker.is_ready", new_callable=AsyncMock, return_value=False),
            patch("app.main._postgres_reachable", new_callable=AsyncMock, return_value=False),
        ):
            response = await client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    async def test_readiness_is_green_when_everything_is_reachable(
        self, client: AsyncClient
    ) -> None:
        with (
            patch("app.main.broker.is_ready", new_callable=AsyncMock, return_value=True),
            patch("app.main._postgres_reachable", new_callable=AsyncMock, return_value=True),
        ):
            response = await client.get("/health/ready")

        assert response.status_code == 200
        assert response.json() == {
            "ready": True,
            "dependencies": {"postgres": True, "rabbitmq": True},
        }

    async def test_readiness_names_the_dependency_that_is_down(
        self, client: AsyncClient
    ) -> None:
        with (
            patch("app.main.broker.is_ready", new_callable=AsyncMock, return_value=False),
            patch("app.main._postgres_reachable", new_callable=AsyncMock, return_value=True),
        ):
            response = await client.get("/health/ready")

        assert response.status_code == 503
        assert response.json()["dependencies"] == {"postgres": True, "rabbitmq": False}


class TestQueueOutage:
    async def test_upload_reports_503_when_the_broker_is_down(
        self, client: AsyncClient
    ) -> None:
        from app.exceptions import QueueUnavailableError

        with patch(
            "app.routers.upload.publish_job",
            new_callable=AsyncMock,
            side_effect=QueueUnavailableError(),
        ):
            response = await client.post("/api/upload", files=[csv_part()])

        assert response.status_code == 503
        assert "unavailable" in response.json()["detail"]
