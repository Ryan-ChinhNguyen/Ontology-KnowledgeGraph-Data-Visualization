import uuid
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from httpx import AsyncClient

from tests.conftest import InMemoryStorage


def table_mock(
    session_id: uuid.UUID,
    name: str,
    *,
    row_count: int = 0,
    columns: tuple[str, ...] = (),
    parquet_path: str | None = None,
) -> MagicMock:
    table = MagicMock()
    table.table_id = uuid.uuid4()
    table.session_id = session_id
    table.row_count = row_count
    table.parquet_path = parquet_path or f"memory://{session_id}/parsed/{name}.parquet"
    table.columns = []
    for position, column in enumerate(columns):
        held = MagicMock(position=position, inferred_type="int64")
        held.name = column
        table.columns.append(held)
    # MagicMock treats `name` as its own attribute, so set it after creation.
    table.name = name
    return table


def link_mock(
    session_id: uuid.UUID,
    from_table: str,
    to_table: str,
    from_column: str | None = "customer_id",
    to_column: str | None = "customer_id",
) -> MagicMock:
    link = MagicMock()
    link.relationship_id = uuid.uuid4()
    link.session_id = session_id
    link.from_table = from_table
    link.to_table = to_table
    link.from_column = from_column
    link.to_column = to_column
    link.type = "FOREIGN_KEY"
    return link


def results(*batches: list) -> list[MagicMock]:
    """One Result per expected `execute`, in the order the service runs them."""
    produced = []
    for batch in batches:
        result = MagicMock()
        result.scalars.return_value = batch
        produced.append(result)
    return produced


def write_parquet(storage: InMemoryStorage, location: str, frame: pd.DataFrame) -> None:
    with storage.writer(location) as stream:
        pq.write_table(pa.Table.from_pandas(frame), stream)


class TestSessionGraph:
    async def test_tables_become_nodes_and_links_become_edges(
        self, client: AsyncClient, db: AsyncMock, stored_session: MagicMock
    ) -> None:
        session_id = stored_session.session_id
        customers = table_mock(session_id, "customers", row_count=3, columns=("customer_id",))
        orders = table_mock(session_id, "orders", row_count=5, columns=("order_id", "customer_id"))

        db.get.return_value = stored_session
        db.execute.side_effect = results(
            [customers, orders],
            [link_mock(session_id, "orders", "customers")],
        )

        response = await client.get(f"/api/sessions/{session_id}/graph")

        assert response.status_code == 200
        body = response.json()
        assert [node["name"] for node in body["nodes"]] == ["customers", "orders"]
        assert body["nodes"][1]["column_count"] == 2

        edge = body["edges"][0]
        assert edge["from_table_id"] == str(orders.table_id)
        assert edge["to_table_id"] == str(customers.table_id)
        assert edge["from_column"] == "customer_id"

    async def test_a_link_to_a_table_this_session_never_parsed_is_dropped(
        self, client: AsyncClient, db: AsyncMock, stored_session: MagicMock
    ) -> None:
        """A dump may declare a foreign key against a table it never creates."""
        session_id = stored_session.session_id
        db.get.return_value = stored_session
        db.execute.side_effect = results(
            [table_mock(session_id, "orders", columns=("customer_id",))],
            [link_mock(session_id, "orders", "customers")],
        )

        response = await client.get(f"/api/sessions/{session_id}/graph")

        assert response.status_code == 200
        assert response.json()["edges"] == []

    async def test_a_session_with_no_links_still_returns_its_nodes(
        self, client: AsyncClient, db: AsyncMock, stored_session: MagicMock
    ) -> None:
        session_id = stored_session.session_id
        db.get.return_value = stored_session
        db.execute.side_effect = results([table_mock(session_id, "people")], [])

        response = await client.get(f"/api/sessions/{session_id}/graph")

        assert response.status_code == 200
        body = response.json()
        assert len(body["nodes"]) == 1
        assert body["edges"] == []

    async def test_an_unknown_session_is_not_found(
        self, client: AsyncClient, db: AsyncMock
    ) -> None:
        db.get.return_value = None

        response = await client.get(f"/api/sessions/{uuid.uuid4()}/graph")

        assert response.status_code == 404


class TestTableGraph:
    def setup_graph(
        self, db: AsyncMock, storage: InMemoryStorage, session_id: uuid.UUID
    ) -> MagicMock:
        orders = table_mock(session_id, "orders", row_count=3)
        customers = table_mock(session_id, "customers", row_count=2)

        write_parquet(
            storage,
            orders.parquet_path,
            pd.DataFrame({"order_id": [10, 11, 12], "customer_id": [1, 2, 1]}),
        )
        write_parquet(
            storage,
            customers.parquet_path,
            pd.DataFrame({"customer_id": [1, 2], "name": ["Alice", "Bob"]}),
        )

        db.get.return_value = orders
        db.execute.side_effect = results(
            [link_mock(session_id, "orders", "customers")],
            [customers],
        )
        return orders

    async def test_rows_are_joined_to_the_rows_they_reference(
        self,
        client: AsyncClient,
        db: AsyncMock,
        storage: InMemoryStorage,
        stored_session: MagicMock,
    ) -> None:
        orders = self.setup_graph(db, storage, stored_session.session_id)

        response = await client.get(f"/api/tables/{orders.table_id}/graph")

        assert response.status_code == 200
        body = response.json()
        assert body["root_table"] == "orders"
        assert body["truncated"] is False

        # Three orders, plus the two customers they point at.
        assert len(body["nodes"]) == 5
        assert {edge["source"] for edge in body["edges"]} == {
            "orders#0",
            "orders#1",
            "orders#2",
        }
        # Orders 10 and 12 both belong to customer 1.
        assert [edge["target"] for edge in body["edges"]] == [
            "customers#0",
            "customers#1",
            "customers#0",
        ]

    async def test_a_row_nothing_links_to_is_left_out(
        self,
        client: AsyncClient,
        db: AsyncMock,
        storage: InMemoryStorage,
        stored_session: MagicMock,
    ) -> None:
        session_id = stored_session.session_id
        orders = table_mock(session_id, "orders", row_count=1)
        customers = table_mock(session_id, "customers", row_count=2)

        write_parquet(
            storage, orders.parquet_path, pd.DataFrame({"order_id": [10], "customer_id": [1]})
        )
        write_parquet(
            storage,
            customers.parquet_path,
            pd.DataFrame({"customer_id": [1, 2], "name": ["Alice", "Unreferenced"]}),
        )

        db.get.return_value = orders
        db.execute.side_effect = results(
            [link_mock(session_id, "orders", "customers")],
            [customers],
        )

        response = await client.get(f"/api/tables/{orders.table_id}/graph")

        tables = [node["table"] for node in response.json()["nodes"]]
        assert tables == ["orders", "customers"]

    async def test_a_composite_key_is_not_drawn(
        self,
        client: AsyncClient,
        db: AsyncMock,
        storage: InMemoryStorage,
        stored_session: MagicMock,
    ) -> None:
        """Matching a composite key needs several columns compared together."""
        session_id = stored_session.session_id
        orders = table_mock(session_id, "orders", row_count=1)
        write_parquet(
            storage, orders.parquet_path, pd.DataFrame({"order_id": [10], "customer_id": [1]})
        )

        db.get.return_value = orders
        db.execute.side_effect = results(
            [link_mock(session_id, "orders", "customers", "a,b", "c,d")],
            [table_mock(session_id, "customers")],
        )

        response = await client.get(f"/api/tables/{orders.table_id}/graph")

        assert response.status_code == 200
        assert response.json()["edges"] == []

    async def test_more_rows_than_were_drawn_is_reported(
        self,
        client: AsyncClient,
        db: AsyncMock,
        storage: InMemoryStorage,
        stored_session: MagicMock,
    ) -> None:
        session_id = stored_session.session_id
        orders = table_mock(session_id, "orders", row_count=500)
        write_parquet(storage, orders.parquet_path, pd.DataFrame({"order_id": [10, 11]}))

        db.get.return_value = orders
        db.execute.side_effect = results([], [])

        response = await client.get(f"/api/tables/{orders.table_id}/graph?limit=2")

        assert response.json()["truncated"] is True

    async def test_a_link_declared_twice_draws_one_edge(
        self,
        client: AsyncClient,
        db: AsyncMock,
        storage: InMemoryStorage,
        stored_session: MagicMock,
    ) -> None:
        """A dump can declare a key inline and again as a constraint."""
        session_id = stored_session.session_id
        orders = table_mock(session_id, "orders", row_count=1)
        customers = table_mock(session_id, "customers", row_count=1)

        write_parquet(
            storage, orders.parquet_path, pd.DataFrame({"order_id": [10], "customer_id": [1]})
        )
        write_parquet(
            storage, customers.parquet_path, pd.DataFrame({"customer_id": [1], "name": ["Alice"]})
        )

        db.get.return_value = orders
        db.execute.side_effect = results(
            [
                link_mock(session_id, "orders", "customers"),
                link_mock(session_id, "orders", "customers"),
            ],
            [customers],
        )

        response = await client.get(f"/api/tables/{orders.table_id}/graph")

        assert len(response.json()["edges"]) == 1

    async def test_rows_with_no_join_value_are_not_joined(
        self,
        client: AsyncClient,
        db: AsyncMock,
        storage: InMemoryStorage,
        stored_session: MagicMock,
    ) -> None:
        """Two nulls are not a shared value, however they are stored."""
        session_id = stored_session.session_id
        orders = table_mock(session_id, "orders", row_count=2)
        customers = table_mock(session_id, "customers", row_count=1)

        write_parquet(
            storage,
            orders.parquet_path,
            pd.DataFrame({"order_id": [10, 11], "customer_id": [None, 1]}),
        )
        write_parquet(
            storage,
            customers.parquet_path,
            pd.DataFrame({"customer_id": [None], "name": ["Nobody"]}),
        )

        db.get.return_value = orders
        db.execute.side_effect = results(
            [link_mock(session_id, "orders", "customers")],
            [customers],
        )

        response = await client.get(f"/api/tables/{orders.table_id}/graph")

        assert response.json()["edges"] == []

    async def test_an_unknown_table_is_not_found(
        self, client: AsyncClient, db: AsyncMock
    ) -> None:
        db.get.return_value = None

        response = await client.get(f"/api/tables/{uuid.uuid4()}/graph")

        assert response.status_code == 404

    async def test_the_limit_is_capped(self, client: AsyncClient) -> None:
        response = await client.get(f"/api/tables/{uuid.uuid4()}/graph?limit=5000")

        assert response.status_code == 422
