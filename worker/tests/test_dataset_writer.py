"""What a parse leaves behind: rows in Parquet, a description in the database."""

import uuid
from unittest.mock import MagicMock

import pandas as pd
import pyarrow.parquet as pq
import pytest
from ontology_shared.messaging import JobMessage
from ontology_shared.models import DatasetColumn, DatasetRelationship, DatasetTable
from sqlalchemy import select

from app.core.database import session_factory
from app.core.storage import storage
from app.parsers.base import Column, NormalizedData, Relationship, Table
from app.services import job_processor
from app.services.job_processor import Outcome, process_job


def parsed_people() -> NormalizedData:
    frame = pd.DataFrame({"id": [1, 2], "name": ["alice", "bob"]})
    return NormalizedData(
        tables=[
            Table(
                name="people",
                columns=[Column("id", "int64"), Column("name", "object")],
                frame=frame,
            )
        ]
    )


@pytest.fixture
def parser(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    stub = MagicMock()
    stub.parse.return_value = parsed_people()
    monkeypatch.setattr(job_processor, "parser_for", lambda _format: stub)
    return stub


async def tables_of(session_id: uuid.UUID) -> list[DatasetTable]:
    async with session_factory() as db:
        result = await db.execute(
            select(DatasetTable)
            .where(DatasetTable.session_id == session_id)
            .order_by(DatasetTable.name)
        )
        return list(result.scalars())


async def columns_of(table_id: uuid.UUID) -> list[DatasetColumn]:
    async with session_factory() as db:
        result = await db.execute(
            select(DatasetColumn)
            .where(DatasetColumn.table_id == table_id)
            .order_by(DatasetColumn.position)
        )
        return list(result.scalars())


class TestRecordingWhatWasParsed:
    async def test_records_each_table_with_where_its_rows_went(
        self, stored_job: JobMessage, parser: MagicMock
    ) -> None:
        await process_job(stored_job, attempt=1, is_final_attempt=False)

        tables = await tables_of(stored_job.session_id)
        assert [table.name for table in tables] == ["people"]
        assert tables[0].row_count == 2
        assert tables[0].parquet_path.endswith("people.parquet")

    async def test_records_columns_in_file_order(
        self, stored_job: JobMessage, parser: MagicMock
    ) -> None:
        await process_job(stored_job, attempt=1, is_final_attempt=False)

        table = (await tables_of(stored_job.session_id))[0]
        columns = await columns_of(table.table_id)
        assert [(c.position, c.name, c.inferred_type) for c in columns] == [
            (0, "id", "int64"),
            (1, "name", "object"),
        ]

    async def test_writes_the_rows_where_it_says_it_did(
        self, stored_job: JobMessage, parser: MagicMock
    ) -> None:
        await process_job(stored_job, attempt=1, is_final_attempt=False)

        table = (await tables_of(stored_job.session_id))[0]
        with storage.reader(table.parquet_path) as stream:
            written = pq.read_table(stream).to_pandas()

        assert written.to_dict(orient="records") == [
            {"id": 1, "name": "alice"},
            {"id": 2, "name": "bob"},
        ]

    async def test_parquet_is_smaller_than_the_rows_it_holds(
        self, stored_job: JobMessage, parser: MagicMock
    ) -> None:
        """Why the rows are not kept in the database: the file is compact and
        can be read a piece at a time."""
        parser.parse.return_value = NormalizedData(
            tables=[
                Table(
                    name="people",
                    columns=[Column("id", "int64"), Column("city", "object")],
                    frame=pd.DataFrame({"id": range(20000), "city": ["Hanoi"] * 20000}),
                )
            ]
        )

        await process_job(stored_job, attempt=1, is_final_attempt=False)

        table = (await tables_of(stored_job.session_id))[0]
        with storage.reader(table.parquet_path) as stream:
            written_bytes = len(stream.read())
        assert written_bytes < 20000 * len("0,Hanoi\n")

    async def test_records_declared_links_between_tables(
        self, stored_job: JobMessage, parser: MagicMock
    ) -> None:
        data = parsed_people()
        data.tables[0].relationships = [Relationship("people", "orders", "FOREIGN_KEY")]
        parser.parse.return_value = data

        await process_job(stored_job, attempt=1, is_final_attempt=False)

        async with session_factory() as db:
            links = list(
                (
                    await db.execute(
                        select(DatasetRelationship).where(
                            DatasetRelationship.session_id == stored_job.session_id
                        )
                    )
                ).scalars()
            )
        assert [(l.from_table, l.to_table, l.type) for l in links] == [
            ("people", "orders", "FOREIGN_KEY")
        ]

    async def test_the_same_link_declared_twice_is_recorded_once(
        self, stored_job: JobMessage, parser: MagicMock
    ) -> None:
        """A dump can carry a key inline on the column and again as a constraint."""
        data = parsed_people()
        data.tables[0].relationships = [
            Relationship("people", "orders", "FOREIGN_KEY", "id", "person_id"),
            Relationship("people", "orders", "FOREIGN_KEY", "id", "person_id"),
        ]
        parser.parse.return_value = data

        await process_job(stored_job, attempt=1, is_final_attempt=False)

        async with session_factory() as db:
            links = list(
                (
                    await db.execute(
                        select(DatasetRelationship).where(
                            DatasetRelationship.session_id == stored_job.session_id
                        )
                    )
                ).scalars()
            )
        assert len(links) == 1
        assert (links[0].from_column, links[0].to_column) == ("id", "person_id")

    async def test_running_again_replaces_rather_than_duplicates(
        self, stored_job: JobMessage, parser: MagicMock
    ) -> None:
        await process_job(stored_job, attempt=1, is_final_attempt=False)
        async with session_factory() as db:
            from ontology_shared.models import Job, JobStatus

            await db.execute(
                Job.__table__.update()
                .where(Job.job_id == stored_job.job_id)
                .values(status=JobStatus.queued)
            )
            await db.commit()

        result = await process_job(stored_job, attempt=2, is_final_attempt=False)

        assert result.outcome is Outcome.PROCESSED
        assert len(await tables_of(stored_job.session_id)) == 1

    async def test_records_nothing_when_the_parse_fails(
        self, stored_job: JobMessage, parser: MagicMock
    ) -> None:
        parser.parse.side_effect = ValueError("unreadable file")

        with pytest.raises(ValueError):
            await process_job(stored_job, attempt=1, is_final_attempt=False)

        assert await tables_of(stored_job.session_id) == []
