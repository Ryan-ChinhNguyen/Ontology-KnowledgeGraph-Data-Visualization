"""Reads a PostgreSQL dump.

The dump is parsed, never executed: statements are turned into an AST and read
for structure, so ``DROP``, ``TRUNCATE``, and shell escapes in an uploaded file
have no effect beyond being skipped.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import expressions as exp

from app.parsers.base import BaseParser, Column, NormalizedData, Relationship, Table

log = logging.getLogger(__name__)

DIALECT = "postgres"

#: Statement types carrying no schema or row information. Skipping them also
#: means a destructive statement is never interpreted.
IGNORED_STATEMENTS = (exp.Drop, exp.Command, exp.Use, exp.Set, exp.Alter)

UNKNOWN_TYPE = "unknown"


@dataclass
class _ParseState:
    """Accumulates the dump's contents across statements and files.

    ``declared_columns`` caches each table's column names so that an
    ``INSERT`` without a column list does not rebuild them every time — dumps
    written with ``pg_dump --inserts`` emit one such statement per row.
    """

    tables: dict[str, Table] = field(default_factory=dict)
    foreign_keys: list[Relationship] = field(default_factory=list)
    declared_columns: dict[str, list[str]] = field(default_factory=dict)


class SqlParser(BaseParser):
    def parse(self, file_paths: list[str]) -> NormalizedData:
        state = _ParseState()

        for path in file_paths:
            statements = sqlglot.parse(
                Path(path).read_text(encoding="utf-8"),
                dialect=DIALECT,
                error_level=sqlglot.ErrorLevel.WARN,
            )
            for statement in statements:
                self._apply(statement, state)

        # Applied after every CREATE has been seen, so a foreign key declared
        # before its target table still resolves.
        for relationship in state.foreign_keys:
            table = state.tables.get(relationship.from_table)
            if table is not None:
                table.relationships.append(relationship)

        return NormalizedData(tables=list(state.tables.values()))

    def _apply(self, statement: exp.Expression | None, state: _ParseState) -> None:
        if statement is None or isinstance(statement, IGNORED_STATEMENTS):
            return

        if isinstance(statement, exp.Create) and isinstance(statement.this, exp.Schema):
            table = self._read_create(statement, state.foreign_keys)
            state.tables[table.name] = table
            state.declared_columns[table.name] = [column.name for column in table.columns]
            return

        if isinstance(statement, exp.Insert):
            self._read_insert(statement, state)

    def _read_create(self, statement: exp.Create, foreign_keys: list[Relationship]) -> Table:
        schema: exp.Schema = statement.this
        table_name = schema.this.name

        columns: list[Column] = []
        for definition in schema.expressions:
            if isinstance(definition, exp.ColumnDef):
                columns.append(Column(name=definition.name, inferred_type=self._type_of(definition)))
            elif isinstance(definition, exp.ForeignKey):
                target = self._foreign_key_target(definition)
                if target:
                    foreign_keys.append(
                        Relationship(from_table=table_name, to_table=target, type="FOREIGN_KEY")
                    )

        return Table(name=table_name, columns=columns)

    def _type_of(self, definition: exp.ColumnDef) -> str:
        kind = definition.args.get("kind")
        return kind.sql(dialect=DIALECT) if kind else UNKNOWN_TYPE

    def _foreign_key_target(self, definition: exp.ForeignKey) -> str | None:
        reference = definition.args.get("reference")
        if reference is None:
            return None
        target = reference.find(exp.Table)
        return target.name if target else None

    def _read_insert(self, statement: exp.Insert, state: _ParseState) -> None:
        """Attach rows to an already-declared table.

        Rows for a table with no CREATE are dropped: without the column list
        there is nothing meaningful to key the values by.
        """
        values = statement.args.get("expression")
        if not isinstance(values, exp.Values):
            return

        destination = self._insert_target(statement)
        if destination is None:
            return
        table_name, listed_columns = destination

        target = state.tables.get(table_name)
        if target is None:
            log.warning("Skipping INSERT for undeclared table '%s'", table_name)
            return

        column_names = listed_columns or state.declared_columns.get(table_name, [])
        for tuple_expression in values.expressions:
            literals = [self._literal(value) for value in tuple_expression.expressions]
            target.rows.append(dict(zip(column_names, literals)))

    def _insert_target(self, statement: exp.Insert) -> tuple[str, list[str]] | None:
        """Read the destination table and its column list off the statement.

        Both live directly under ``this``, so they are read in constant time
        rather than by searching the statement — whose ``VALUES`` subtree grows
        with the number of rows being inserted.
        """
        destination = statement.this

        if isinstance(destination, exp.Schema):
            table = destination.this
            columns = [column.name for column in destination.expressions]
        elif isinstance(destination, exp.Table):
            table, columns = destination, []
        else:
            return None

        return (table.name, columns) if isinstance(table, exp.Table) else None

    def _literal(self, value: exp.Expression) -> object:
        if isinstance(value, exp.Null):
            return None
        if isinstance(value, exp.Boolean):
            return value.this
        if isinstance(value, exp.Literal):
            return value.this if value.is_string else self._number(value.this)
        return value.sql(dialect=DIALECT)

    def _number(self, raw: str) -> object:
        try:
            return int(raw)
        except ValueError:
            try:
                return float(raw)
            except ValueError:
                return raw
