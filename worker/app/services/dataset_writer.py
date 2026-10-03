"""Writes what a parse produced: rows to storage, description to the database.

Rows go to Parquet because they are read whole exactly once, when the graph is
built, and a page at a time in between. Parquet is smaller than the file the
rows came from, can be read a piece at a time, and keeps column types — none
of which a database column of rows would give.

What the database keeps is the description: which tables a session produced,
their columns, and where the rows were written.
"""

import logging
import uuid
from dataclasses import dataclass

import pyarrow as pa
import pyarrow.parquet as pq
from ontology_shared.models import DatasetColumn, DatasetRelationship, DatasetTable
from ontology_shared.storage import FileStorage
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.parsers.base import NormalizedData, Table

log = logging.getLogger(__name__)

PARSED_FOLDER = "parsed"
PARQUET_COMPRESSION = "snappy"


@dataclass(frozen=True)
class StoredTable:
    """A table whose rows are written and which is ready to be recorded."""

    name: str
    row_count: int
    parquet_path: str
    columns: list[tuple[str, str]]


def write_tables(
    storage: FileStorage, session_id: uuid.UUID, data: NormalizedData
) -> list[StoredTable]:
    """Write every parsed table to storage.

    Paths are derived from the session and table name, so a job that runs
    again overwrites its previous output instead of leaving a second copy.
    """
    return [_write_table(storage, session_id, table) for table in data.tables]


def _write_table(storage: FileStorage, session_id: uuid.UUID, table: Table) -> StoredTable:
    location = storage.location(session_id, PARSED_FOLDER, f"{table.name}.parquet")

    with storage.writer(location) as stream:
        pq.write_table(pa.Table.from_pandas(table.frame), stream, compression=PARQUET_COMPRESSION)

    log.info("Wrote table '%s' (%d rows) to %s", table.name, table.row_count, location)
    return StoredTable(
        name=table.name,
        row_count=table.row_count,
        parquet_path=location,
        columns=[(column.name, column.inferred_type) for column in table.columns],
    )


async def record_tables(
    db: AsyncSession,
    session_id: uuid.UUID,
    stored: list[StoredTable],
    data: NormalizedData,
) -> None:
    """Replace what the database says this session parsed into.

    Adding the statements to the caller's transaction is what keeps the job's
    completion and its results from disagreeing: either both are committed or
    neither is.

    Previous rows are removed first so that re-running a job leaves one
    description rather than two.
    """
    await db.execute(delete(DatasetTable).where(DatasetTable.session_id == session_id))
    await db.execute(
        delete(DatasetRelationship).where(DatasetRelationship.session_id == session_id)
    )

    for table in stored:
        record = DatasetTable(
            session_id=session_id,
            name=table.name,
            row_count=table.row_count,
            parquet_path=table.parquet_path,
        )
        record.columns = [
            DatasetColumn(name=name, position=position, inferred_type=inferred_type)
            for position, (name, inferred_type) in enumerate(table.columns)
        ]
        db.add(record)

    # A dump may declare the same foreign key twice — inline on the column and
    # again as a constraint — and storing it twice would draw the same edge
    # twice later on.
    written: set[tuple[str, str, str, str, str]] = set()
    for parsed in data.tables:
        for link in parsed.relationships:
            identity = (
                link.from_table,
                link.to_table,
                link.from_column,
                link.to_column,
                link.type,
            )
            if identity in written:
                continue
            written.add(identity)

            db.add(
                DatasetRelationship(
                    session_id=session_id,
                    from_table=link.from_table,
                    to_table=link.to_table,
                    type=link.type,
                    from_column=link.from_column or None,
                    to_column=link.to_column or None,
                )
            )
