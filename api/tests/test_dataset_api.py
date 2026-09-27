import uuid
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from httpx import AsyncClient

from app.services.dataset_service import read_rows
from tests.conftest import InMemoryStorage


def stored_table(session_id: uuid.UUID, path: str = "memory://t/people.parquet") -> MagicMock:
    table = MagicMock()
    table.table_id = uuid.uuid4()
    table.session_id = session_id
    table.name = "people"
    table.row_count = 2
    table.parquet_path = path
    table.columns = [
        MagicMock(name_="id", position=0, inferred_type="int64"),
        MagicMock(name_="city", position=1, inferred_type="object"),
    ]
    # MagicMock treats `name` as its own attribute, so set it after creation.
    table.columns[0].name = "id"
    table.columns[1].name = "city"
    return table


def write_parquet(storage: InMemoryStorage, location: str, frame: pd.DataFrame) -> None:
    with storage.writer(location) as stream:
        pq.write_table(pa.Table.from_pandas(frame), stream)


class TestListTables:
    async def test_lists_tables_with_their_columns(
        self, client: AsyncClient, db: AsyncMock, stored_session: MagicMock
    ) -> None:
        table = stored_table(stored_session.session_id)
        db.get.return_value = stored_session
        db.execute.return_value.scalars.return_value = [table]

        response = await client.get(f"/api/sessions/{stored_session.session_id}/tables")

        assert response.status_code == 200
        body = response.json()
        assert body["tables"][0]["name"] == "people"
        assert body["tables"][0]["row_count"] == 2
        assert [column["name"] for column in body["tables"][0]["columns"]] == ["id", "city"]

    async def test_a_session_still_being_processed_has_no_tables_yet(
        self, client: AsyncClient, db: AsyncMock, stored_session: MagicMock
    ) -> None:
        db.get.return_value = stored_session
        db.execute.return_value.scalars.return_value = []

        response = await client.get(f"/api/sessions/{stored_session.session_id}/tables")

        assert response.status_code == 200
        assert response.json()["tables"] == []

    async def test_unknown_session_is_not_the_same_as_an_empty_one(
        self, client: AsyncClient, db: AsyncMock
    ) -> None:
        db.get.return_value = None

        response = await client.get(f"/api/sessions/{uuid.uuid4()}/tables")

        assert response.status_code == 404


class TestReadRows:
    @pytest.fixture
    def table_with_rows(self, storage: InMemoryStorage, db: AsyncMock) -> MagicMock:
        table = stored_table(uuid.uuid4())
        table.row_count = 5
        write_parquet(
            storage,
            table.parquet_path,
            pd.DataFrame({"id": range(5), "city": ["Hanoi", "Hue", "HCM", None, "Vinh"]}),
        )
        db.get.return_value = table
        return table

    async def test_returns_a_page_of_rows(
        self, client: AsyncClient, table_with_rows: MagicMock
    ) -> None:
        response = await client.get(f"/api/tables/{table_with_rows.table_id}/rows?offset=1&limit=2")

        assert response.status_code == 200
        body = response.json()
        assert body["rows"] == [{"id": 1, "city": "Hue"}, {"id": 2, "city": "HCM"}]
        assert body["total_rows"] == 5

    async def test_reports_missing_values_as_null(
        self, client: AsyncClient, table_with_rows: MagicMock
    ) -> None:
        response = await client.get(f"/api/tables/{table_with_rows.table_id}/rows?offset=3&limit=1")

        assert response.json()["rows"] == [{"id": 3, "city": None}]

    async def test_paging_past_the_end_returns_nothing(
        self, client: AsyncClient, table_with_rows: MagicMock
    ) -> None:
        response = await client.get(f"/api/tables/{table_with_rows.table_id}/rows?offset=99")

        assert response.status_code == 200
        assert response.json()["rows"] == []

    async def test_rejects_a_page_larger_than_the_limit(
        self, client: AsyncClient, table_with_rows: MagicMock
    ) -> None:
        """A caller cannot ask for the whole of a large table in one response."""
        response = await client.get(f"/api/tables/{table_with_rows.table_id}/rows?limit=5000")

        assert response.status_code == 422

    async def test_unknown_table_returns_404(self, client: AsyncClient, db: AsyncMock) -> None:
        db.get.return_value = None

        response = await client.get(f"/api/tables/{uuid.uuid4()}/rows")

        assert response.status_code == 404


class TestPagingCost:
    def test_reads_only_the_requested_page(self, storage: InMemoryStorage) -> None:
        """The page is what gets turned into rows, not the whole table."""
        table = stored_table(uuid.uuid4())
        write_parquet(
            storage, table.parquet_path, pd.DataFrame({"id": range(50_000)})
        )

        rows = read_rows(table, storage, offset=49_990, limit=5)

        assert rows == [{"id": index} for index in range(49_990, 49_995)]
