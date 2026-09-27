import logging
import uuid

from fastapi import APIRouter, Depends, Query
from ontology_shared.storage import FileStorage
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies import get_storage
from app.models.schemas import DatasetTablesResponse, ErrorResponse, RowPageResponse
from app.services.dataset_service import get_table, list_tables, read_rows

log = logging.getLogger(__name__)

router = APIRouter(tags=["dataset"])

MAX_PAGE_ROWS = 500

NOT_FOUND = {404: {"model": ErrorResponse, "description": "No such session or table."}}


@router.get(
    "/sessions/{session_id}/tables",
    response_model=DatasetTablesResponse,
    summary="List the tables an upload parsed into",
    responses=NOT_FOUND,
)
async def get_session_tables(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DatasetTablesResponse:
    """Report what was found in a session's files.

    Empty while the session is still being processed, and for one that failed.
    """
    return DatasetTablesResponse.build(session_id, await list_tables(session_id, db))


@router.get(
    "/tables/{table_id}/rows",
    response_model=RowPageResponse,
    summary="Read a page of a table's rows",
    responses=NOT_FOUND,
)
async def get_table_rows(
    table_id: uuid.UUID,
    offset: int = Query(0, ge=0, description="Rows to skip."),
    limit: int = Query(50, ge=1, le=MAX_PAGE_ROWS, description="Rows to return."),
    db: AsyncSession = Depends(get_db),
    storage: FileStorage = Depends(get_storage),
) -> RowPageResponse:
    """Return rows from the file the parse wrote.

    Only the requested page is read, so the cost does not grow with the size
    of the table. `total_rows` says how far paging can go.
    """
    table = await get_table(table_id, db)
    rows = read_rows(table, storage, offset=offset, limit=limit)

    return RowPageResponse(
        table_id=table.table_id,
        offset=offset,
        limit=limit,
        total_rows=table.row_count,
        rows=rows,
    )
