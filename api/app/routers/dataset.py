import logging
import uuid

from fastapi import APIRouter, Depends, Query
from ontology_shared.storage import FileStorage
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies import get_storage
from app.models.schemas import (
    DatasetTablesResponse,
    ErrorResponse,
    RowPageResponse,
    SessionGraphResponse,
    TableGraphResponse,
)
from app.services.dataset_service import get_table, list_tables, read_rows
from app.services.graph_service import build_session_graph, build_table_graph

log = logging.getLogger(__name__)

router = APIRouter(tags=["dataset"])

MAX_PAGE_ROWS = 500

#: A row graph stops being readable long before it stops being drawable, so the
#: ceiling here is well below the one for a page of rows.
MAX_GRAPH_ROWS = 200

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
    "/sessions/{session_id}/graph",
    response_model=SessionGraphResponse,
    summary="The upload's tables and their links, as a graph",
    responses=NOT_FOUND,
)
async def get_session_graph(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> SessionGraphResponse:
    """Return the session as nodes and edges.

    Only formats that declare their own links produce edges — a SQL dump's
    foreign keys. The nodes are the same tables `/tables` lists, so a session
    with no links still returns a graph, just one with nothing joining it up.
    """
    return SessionGraphResponse.model_validate(await build_session_graph(session_id, db))


@router.get(
    "/tables/{table_id}/graph",
    response_model=TableGraphResponse,
    summary="One table's rows and the rows they link to, as a graph",
    responses=NOT_FOUND,
)
async def get_table_graph(
    table_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=MAX_GRAPH_ROWS, description="Rows of this table to draw."),
    db: AsyncSession = Depends(get_db),
    storage: FileStorage = Depends(get_storage),
) -> TableGraphResponse:
    """Drill down from a table to the rows inside it.

    Rows of the chosen table are all drawn; a row of a neighbouring table is
    drawn only where a link reaches it. `truncated` says whether the table held
    more rows than were drawn.
    """
    return TableGraphResponse.model_validate(
        await build_table_graph(table_id, db, storage, limit=limit)
    )


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
