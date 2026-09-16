from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

from httpx import AsyncClient

from app.middleware import MULTIPART_OVERHEAD_BYTES

MB = 1024 * 1024
LIMIT_BYTES = 20 * MB
BOUNDARY = "sizelimit"


class StreamedUpload:
    """A multipart body sent chunk by chunk, recording how much was taken.

    The size rule is also enforced after parsing, so a 413 alone cannot show
    that a request was stopped early. Counting the chunks the server pulled
    can.
    """

    def __init__(self, megabytes: int) -> None:
        self.megabytes = megabytes
        self.chunks_sent = 0

    @property
    def length(self) -> int:
        return len(self._head()) + self.megabytes * MB + len(self._tail())

    async def body(self) -> AsyncIterator[bytes]:
        yield self._head()
        for _ in range(self.megabytes):
            self.chunks_sent += 1
            yield b"x" * MB
        yield self._tail()

    def _head(self) -> bytes:
        return (
            f"--{BOUNDARY}\r\n"
            'Content-Disposition: form-data; name="files"; filename="big.csv"\r\n'
            "Content-Type: text/csv\r\n\r\n"
        ).encode()

    def _tail(self) -> bytes:
        return f"\r\n--{BOUNDARY}--\r\n".encode()


CONTENT_TYPE = {"content-type": f"multipart/form-data; boundary={BOUNDARY}"}


class TestDeclaredLength:
    async def test_rejects_without_reading_the_body(self, client: AsyncClient) -> None:
        """A client that declares an oversized request is turned away before
        the service reads any of it."""
        upload = StreamedUpload(megabytes=25)

        response = await client.post(
            "/api/upload",
            content=upload.body(),
            headers={**CONTENT_TYPE, "content-length": str(upload.length)},
        )

        assert response.status_code == 413
        assert "20MB" in response.json()["detail"]
        assert upload.chunks_sent == 0

    async def test_lets_a_request_under_the_limit_through(
        self, client: AsyncClient, publisher: AsyncMock
    ) -> None:
        response = await client.post(
            "/api/upload",
            files=[("files", ("people.csv", b"id,name\n1,alice\n", "text/csv"))],
        )

        assert response.status_code == 201

    async def test_leaves_room_for_the_multipart_envelope(
        self, client: AsyncClient, publisher: AsyncMock
    ) -> None:
        """A file right at the limit must not be rejected because boundaries
        and part headers push the request itself over it."""
        body = b"id\n" + b"1\n" * ((LIMIT_BYTES - 3) // 2)

        response = await client.post(
            "/api/upload", files=[("files", ("edge.csv", body, "text/csv"))]
        )

        assert response.status_code == 201
        assert len(body) <= LIMIT_BYTES < len(body) + MULTIPART_OVERHEAD_BYTES


class TestUndeclaredLength:
    async def test_stops_reading_once_the_limit_is_passed(self, client: AsyncClient) -> None:
        """Without Content-Length the size is only known as bytes arrive, so
        the request is cut off where it crosses the limit rather than read to
        the end."""
        upload = StreamedUpload(megabytes=25)

        response = await client.post("/api/upload", content=upload.body(), headers=CONTENT_TYPE)

        assert response.status_code == 413
        assert "20MB" in response.json()["detail"]
        assert upload.chunks_sent < upload.megabytes


class TestOtherEndpoints:
    async def test_requests_without_a_body_are_unaffected(self, client: AsyncClient) -> None:
        response = await client.get("/health")
        assert response.status_code == 200
