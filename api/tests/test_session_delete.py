import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from ontology_shared.models import SessionStatus

from app.exceptions import SessionInUseError, SessionNotFoundError
from app.services.session_service import delete_session
from tests.conftest import InMemoryStorage

FINISHED = [SessionStatus.ready, SessionStatus.failed]
UNFINISHED = [SessionStatus.uploading, SessionStatus.queued, SessionStatus.processing]


class TestDeleteEndpoint:
    @pytest.mark.parametrize("status", FINISHED)
    async def test_removes_a_finished_session(
        self,
        client: AsyncClient,
        db: AsyncMock,
        stored_session: MagicMock,
        status: SessionStatus,
    ) -> None:
        stored_session.status = status
        db.get.return_value = stored_session

        response = await client.delete(f"/api/sessions/{stored_session.session_id}")

        assert response.status_code == 204
        db.delete.assert_awaited_once_with(stored_session)
        db.commit.assert_awaited_once()

    @pytest.mark.parametrize("status", UNFINISHED)
    async def test_refuses_while_the_worker_may_still_need_the_files(
        self,
        client: AsyncClient,
        db: AsyncMock,
        stored_session: MagicMock,
        status: SessionStatus,
    ) -> None:
        stored_session.status = status
        db.get.return_value = stored_session

        response = await client.delete(f"/api/sessions/{stored_session.session_id}")

        assert response.status_code == 409
        assert status.value in response.json()["detail"]
        db.delete.assert_not_awaited()

    async def test_returns_404_for_an_unknown_session(
        self, client: AsyncClient, db: AsyncMock
    ) -> None:
        db.get.return_value = None
        unknown = uuid.uuid4()

        response = await client.delete(f"/api/sessions/{unknown}")

        assert response.status_code == 404
        assert str(unknown) in response.json()["detail"]

    async def test_rejects_a_malformed_session_id(self, client: AsyncClient) -> None:
        response = await client.delete("/api/sessions/not-a-uuid")
        assert response.status_code == 422


class TestDeleteService:
    async def test_removes_the_stored_files(
        self, db: AsyncMock, storage: InMemoryStorage, stored_session: MagicMock
    ) -> None:
        stored_session.status = SessionStatus.ready
        db.get.return_value = stored_session
        storage.save(stored_session.session_id, "people.csv", b"id\n1\n")

        await delete_session(stored_session.session_id, db, storage)

        assert storage.saved == {}
        assert storage.deleted == [stored_session.session_id]

    async def test_removes_files_before_the_record(
        self, db: AsyncMock, storage: InMemoryStorage, stored_session: MagicMock
    ) -> None:
        """The record is what makes the files findable, so it outlives them —
        an interrupted delete then leaves something retryable rather than
        files nothing points to."""
        stored_session.status = SessionStatus.failed
        db.get.return_value = stored_session
        order: list[str] = []
        storage.delete = lambda session_id: order.append("files")  # type: ignore[assignment]
        db.delete = AsyncMock(side_effect=lambda _: order.append("record"))

        await delete_session(stored_session.session_id, db, storage)

        assert order == ["files", "record"]

    async def test_leaves_the_record_when_file_removal_fails(
        self, db: AsyncMock, storage: InMemoryStorage, stored_session: MagicMock
    ) -> None:
        stored_session.status = SessionStatus.ready
        db.get.return_value = stored_session
        storage.delete = MagicMock(side_effect=OSError("disk busy"))  # type: ignore[assignment]

        with pytest.raises(OSError):
            await delete_session(stored_session.session_id, db, storage)

        db.delete.assert_not_awaited()
        db.commit.assert_not_awaited()

    async def test_raises_for_an_unfinished_session(
        self, db: AsyncMock, storage: InMemoryStorage, stored_session: MagicMock
    ) -> None:
        stored_session.status = SessionStatus.processing
        db.get.return_value = stored_session

        with pytest.raises(SessionInUseError):
            await delete_session(stored_session.session_id, db, storage)

        assert storage.deleted == []

    async def test_raises_for_a_missing_session(
        self, db: AsyncMock, storage: InMemoryStorage
    ) -> None:
        db.get.return_value = None

        with pytest.raises(SessionNotFoundError):
            await delete_session(uuid.uuid4(), db, storage)


class TestLocalStorageDelete:
    def test_removing_what_is_already_gone_succeeds(self, tmp_path) -> None:
        """Retrying a delete that failed part-way must not fail on the files
        it already removed."""
        from app.services.storage import LocalFileStorage

        storage = LocalFileStorage(tmp_path)
        session_id = uuid.uuid4()

        storage.delete(session_id)
        storage.delete(session_id)

    def test_removes_only_the_named_session(self, tmp_path) -> None:
        from app.services.storage import LocalFileStorage

        storage = LocalFileStorage(tmp_path)
        doomed, kept = uuid.uuid4(), uuid.uuid4()
        storage.save(doomed, "a.csv", b"a")
        survivor = storage.save(kept, "b.csv", b"b")

        storage.delete(doomed)

        assert not (tmp_path / str(doomed)).exists()
        assert (tmp_path / str(kept)).exists()
        from pathlib import Path

        assert Path(survivor).read_bytes() == b"b"
