"""Shaping what an upload parsed into as a graph.

The database already holds the two halves — tables and the links between them —
but it holds them the way a relational store does, keyed by name. A graph view
needs them keyed by node, so this is where names are resolved to identifiers
and the two queries are stitched into nodes and edges.
"""

import logging
import math
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from ontology_shared.models import DatasetRelationship, DatasetTable, Session
from ontology_shared.storage import FileStorage
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import SessionNotFoundError, TableNotFoundError
from app.services.dataset_service import read_rows

log = logging.getLogger(__name__)

#: Rows pulled from a neighbouring table when expanding one table's rows. The
#: root table's own page is what the caller asks for; this caps the cost of the
#: tables it links to, which the caller never named.
NEIGHBOUR_ROW_CAP = 500

#: Separates the parts of a composite key, as the parser joined them.
COLUMN_SEPARATOR = ","

#: A join column that repeats on both sides multiplies out: 200 rows each
#: matching 500 would be 100,000 edges, long past the point where the picture
#: says anything. Drawing stops here and the result is marked as partial.
MAX_ROW_EDGES = 2000


@dataclass(frozen=True)
class GraphNode:
    table_id: uuid.UUID
    name: str
    row_count: int
    column_count: int


@dataclass(frozen=True)
class GraphEdge:
    relationship_id: uuid.UUID
    from_table_id: uuid.UUID
    to_table_id: uuid.UUID
    from_table: str
    to_table: str
    from_column: str | None
    to_column: str | None
    type: str
    name: str | None
    inverse_name: str | None


@dataclass(frozen=True)
class SessionGraph:
    session_id: uuid.UUID
    nodes: list[GraphNode]
    edges: list[GraphEdge]


@dataclass(frozen=True)
class RowNode:
    id: str
    table: str
    label: str
    row: dict[str, Any]


@dataclass(frozen=True)
class RowEdge:
    id: str
    source: str
    target: str
    label: str


@dataclass(frozen=True)
class TableGraph:
    table_id: uuid.UUID
    root_table: str
    nodes: list[RowNode]
    edges: list[RowEdge]
    #: True when the root table holds more rows than were drawn, so the caller
    #: can say the picture is a sample rather than the whole table.
    truncated: bool


async def build_session_graph(session_id: uuid.UUID, db: AsyncSession) -> SessionGraph:
    """The tables of a session as nodes, and their declared links as edges.

    Both halves are fetched in one query each, and the edges are resolved
    against an index of the nodes — so the cost is linear in the number of
    tables and links rather than one lookup per edge.
    """
    if await db.get(Session, session_id) is None:
        raise SessionNotFoundError(str(session_id))

    tables = await db.execute(
        select(DatasetTable)
        .where(DatasetTable.session_id == session_id)
        .options(selectinload(DatasetTable.columns))
        .order_by(DatasetTable.name)
    )
    nodes = [
        GraphNode(
            table_id=table.table_id,
            name=table.name,
            row_count=table.row_count,
            column_count=len(table.columns),
        )
        for table in tables.scalars()
    ]
    by_name = {node.name: node.table_id for node in nodes}

    links = await db.execute(
        select(DatasetRelationship)
        .where(DatasetRelationship.session_id == session_id)
        .order_by(DatasetRelationship.from_table, DatasetRelationship.to_table)
    )

    edges: list[GraphEdge] = []
    for link in links.scalars():
        source = by_name.get(link.from_table)
        target = by_name.get(link.to_table)
        # A dump may declare a foreign key against a table it never creates.
        # The link is real, but an edge with only one end cannot be drawn, so
        # it is left out rather than returned as a half-edge the caller has to
        # filter again.
        if source is None or target is None:
            log.info(
                "Dropping link %s -> %s: endpoint missing from this session",
                link.from_table,
                link.to_table,
            )
            continue

        edges.append(
            GraphEdge(
                relationship_id=link.relationship_id,
                from_table_id=source,
                to_table_id=target,
                from_table=link.from_table,
                to_table=link.to_table,
                from_column=link.from_column,
                to_column=link.to_column,
                type=link.type,
                name=link.name,
                inverse_name=link.inverse_name,
            )
        )

    return SessionGraph(session_id=session_id, nodes=nodes, edges=edges)


async def build_table_graph(
    table_id: uuid.UUID,
    db: AsyncSession,
    storage: FileStorage,
    *,
    limit: int,
) -> TableGraph:
    """One table's rows as nodes, joined to the rows they reference.

    This is the drill-down from the session graph: where that one says two
    tables are linked, this says which rows the link actually connects.

    A page of the chosen table is read, each neighbouring table is read once up
    to a cap, and the two are matched through an index on the join column — so
    the work is linear in the rows read rather than a scan per row.
    """
    table = await db.get(DatasetTable, table_id)
    if table is None:
        raise TableNotFoundError(str(table_id))

    found_links = await db.execute(
        select(DatasetRelationship).where(
            DatasetRelationship.session_id == table.session_id,
            or_(
                DatasetRelationship.from_table == table.name,
                DatasetRelationship.to_table == table.name,
            ),
        )
    )
    links = list(found_links.scalars())

    # Links that cannot be followed are discarded before anything is read, so a
    # neighbouring table is never loaded for an edge that was not going to be
    # drawn.
    usable = [(link, columns) for link in links if (columns := _column_pair(link)) is not None]

    rows_by_table: dict[str, list[dict[str, Any]]] = {
        table.name: read_rows(table, storage, offset=0, limit=limit)
    }

    neighbour_names = {
        other
        for link, _ in usable
        for other in (link.from_table, link.to_table)
        if other != table.name
    }
    if neighbour_names:
        found = await db.execute(
            select(DatasetTable).where(
                DatasetTable.session_id == table.session_id,
                DatasetTable.name.in_(neighbour_names),
            )
        )
        for record in found.scalars():
            rows_by_table[record.name] = read_rows(
                record, storage, offset=0, limit=NEIGHBOUR_ROW_CAP
            )

    edges: list[RowEdge] = []
    seen: set[str] = set()
    capped = False

    for link, columns in usable:
        source_rows = rows_by_table.get(link.from_table)
        target_rows = rows_by_table.get(link.to_table)
        if source_rows is None or target_rows is None:
            continue

        from_column, to_column = columns
        for edge in _match(
            source=(link.from_table, source_rows, from_column),
            target=(link.to_table, target_rows, to_column),
            label=link.name or f"{from_column} → {to_column}",
        ):
            # A dump can declare the same foreign key twice — inline on the
            # column and again as a constraint — and the two would otherwise
            # produce the same edge, which the renderer rejects as a duplicate.
            if edge.id in seen:
                continue
            if len(edges) >= MAX_ROW_EDGES:
                capped = True
                break

            seen.add(edge.id)
            edges.append(edge)

        if capped:
            log.info("Row graph for '%s' hit the edge cap", table.name)
            break

    # Every row of the chosen table is drawn, including the ones nothing links
    # to; a neighbouring row is drawn only where an edge reaches it, so the
    # picture stays about the table that was opened.
    connected = {edge.source for edge in edges} | {edge.target for edge in edges}
    nodes = [
        _row_node(name, index, row)
        for name, rows in rows_by_table.items()
        for index, row in enumerate(rows)
        if name == table.name or f"{name}#{index}" in connected
    ]

    return TableGraph(
        table_id=table.table_id,
        root_table=table.name,
        nodes=nodes,
        edges=edges,
        truncated=capped or table.row_count > len(rows_by_table[table.name]),
    )


def _match(
    *,
    source: tuple[str, list[dict[str, Any]], str],
    target: tuple[str, list[dict[str, Any]], str],
    label: str,
) -> list[RowEdge]:
    """Edges from each source row to every target row holding the same value."""
    source_name, source_rows, source_column = source
    target_name, target_rows, target_column = target

    index: dict[str, list[str]] = defaultdict(list)
    for position, row in enumerate(target_rows):
        key = _key(row.get(target_column))
        if key is not None:
            index[key].append(f"{target_name}#{position}")

    edges: list[RowEdge] = []
    for position, row in enumerate(source_rows):
        key = _key(row.get(source_column))
        if key is None:
            continue

        source_id = f"{source_name}#{position}"
        for target_id in index.get(key, ()):
            edges.append(
                RowEdge(
                    id=f"{source_id}|{target_id}|{label}",
                    source=source_id,
                    target=target_id,
                    label=label,
                )
            )

    return edges


def _column_pair(link: DatasetRelationship) -> tuple[str, str] | None:
    """The single column on each side of a link, where there is one.

    A link with no columns cannot be followed, and a composite key would need
    the values of several columns matched together — neither is drawn here.
    """
    if not link.from_column or not link.to_column:
        return None
    if COLUMN_SEPARATOR in link.from_column or COLUMN_SEPARATOR in link.to_column:
        log.info(
            "Skipping composite link %s -> %s in the row graph",
            link.from_table,
            link.to_table,
        )
        return None
    return link.from_column, link.to_column


def _key(value: Any) -> str | None:
    """A join value in a form both sides agree on, or None if it cannot join.

    Parquet can hand back an integer column as a float once it has held a null,
    so the two sides of a key are compared as text with whole floats narrowed
    first — otherwise 1 and 1.0 would never meet. A missing value and a NaN are
    not values two rows can share, so they join nothing rather than joining
    every other missing value.
    """
    if value is None:
        return None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        if value.is_integer():
            return str(int(value))
    return str(value)


def _row_node(table_name: str, index: int, row: dict[str, Any]) -> RowNode:
    label = next((str(value) for value in row.values() if value is not None), f"row {index + 1}")
    return RowNode(id=f"{table_name}#{index}", table=table_name, label=label[:32], row=row)
