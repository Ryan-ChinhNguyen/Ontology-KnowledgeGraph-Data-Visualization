"""Reading back what an upload parsed into.

The database says which tables a session produced and where their rows were
written; the rows themselves are read from storage on demand. Nothing is
duplicated into the database for display, so there is one copy of the data and
no second copy to keep in step.
"""

import logging
import uuid
from typing import Any, BinaryIO

import pyarrow.parquet as pq
from ontology_shared.models import DatasetTable, Session
from ontology_shared.storage import FileStorage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import SessionNotFoundError, TableNotFoundError

log = logging.getLogger(__name__)

#: Reading is done in batches of at least this many rows, so a page never
#: costs a pass over the whole file.
MIN_BATCH_ROWS = 1024


async def list_tables(session_id: uuid.UUID, db: AsyncSession) -> list[DatasetTable]:
    """The tables a session parsed into, with their columns.

    A session that exists but has parsed nothing yet has no tables, which is
    different from a session that does not exist at all.
    """
    if await db.get(Session, session_id) is None:
        raise SessionNotFoundError(str(session_id))

    result = await db.execute(
        select(DatasetTable)
        .where(DatasetTable.session_id == session_id)
        .options(selectinload(DatasetTable.columns))
        .order_by(DatasetTable.name)
    )
    return list(result.scalars())


async def get_table(table_id: uuid.UUID, db: AsyncSession) -> DatasetTable:
    table = await db.get(DatasetTable, table_id)
    if table is None:
        raise TableNotFoundError(str(table_id))
    return table


def read_rows(table: DatasetTable, storage: FileStorage, *, offset: int, limit: int) -> list[dict]:
    """Read one page of a table's rows.

    Parquet is read in batches and only the requested page is turned into
    rows, so the cost follows the size of the page rather than the size of the
    file.
    """
    with storage.reader(table.parquet_path) as stream:
        return _read_page(stream, offset=offset, limit=limit)


def _read_page(stream: BinaryIO, *, offset: int, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []

    rows: list[dict[str, Any]] = []
    to_skip = offset

    for batch in pq.ParquetFile(stream).iter_batches(batch_size=max(limit, MIN_BATCH_ROWS)):
        if to_skip >= batch.num_rows:
            to_skip -= batch.num_rows
            continue

        wanted = batch.slice(to_skip, limit - len(rows))
        to_skip = 0
        # Nulls come back as None, which is what the response needs; a row of
        # dictionaries is only built for the page being returned.
        rows.extend(wanted.to_pylist())

        if len(rows) >= limit:
            break

    return rows
