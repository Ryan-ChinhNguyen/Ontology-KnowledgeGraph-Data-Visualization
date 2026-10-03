"""Reads a PostgreSQL dump.

The dump is parsed, never executed: statements are turned into an AST and read
for structure, so ``DROP``, ``TRUNCATE``, and shell escapes in an uploaded file
have no effect beyond being skipped.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import sqlglot
from sqlglot import expressions as exp

from app.parsers.base import (
    BaseParser,
    Column,
    NormalizedData,
    Relationship,
    Table,
    foreign_key_names,
)

log = logging.getLogger(__name__)

DIALECT = "postgres"

#: Statement types carrying no schema or row information. Skipping them also
#: means a destructive statement is never interpreted.
IGNORED_STATEMENTS = (exp.Drop, exp.Command, exp.Use, exp.Set)

UNKNOWN_TYPE = "unknown"

#: Joins the parts of a composite foreign key into a single column reference.
COLUMN_SEPARATOR = ","


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
    #: Rows per table, gathered as the INSERTs are read and turned into frames
    #: once every statement has been seen.
    rows: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))


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

        for name, table in state.tables.items():
            # An explicit column list keeps a table that was declared but never
            # populated from becoming a frame with no columns at all.
            table.frame = pd.DataFrame(
                state.rows.get(name, []), columns=[column.name for column in table.columns]
            )

        return NormalizedData(tables=list(state.tables.values()))

    def _apply(self, statement: exp.Expression | None, state: _ParseState) -> None:
        if statement is None or isinstance(statement, IGNORED_STATEMENTS):
            return

        if isinstance(statement, exp.Create) and isinstance(statement.this, exp.Schema):
            table = self._read_create(statement, state.foreign_keys)
            state.tables[table.name] = table
            state.declared_columns[table.name] = [column.name for column in table.columns]
            return

        if isinstance(statement, exp.Alter):
            self._read_alter(statement, state.foreign_keys)
            return

        if isinstance(statement, exp.Insert):
            self._read_insert(statement, state)

    def _read_alter(self, statement: exp.Alter, foreign_keys: list[Relationship]) -> None:
        """Take the foreign keys out of ``ALTER TABLE ... ADD CONSTRAINT``.

        This is the form ``pg_dump`` writes: the tables are created first and
        their foreign keys added afterwards, so skipping ALTER outright would
        lose every relationship a real dump declares. Only the constraint is
        read — as everywhere else here, nothing is executed.
        """
        table = statement.this
        if not isinstance(table, exp.Table):
            return

        for constraint in statement.find_all(exp.ForeignKey):
            link = self._foreign_key(table.name, constraint)
            if link is not None:
                foreign_keys.append(link)

    def _read_create(self, statement: exp.Create, foreign_keys: list[Relationship]) -> Table:
        schema: exp.Schema = statement.this
        table_name = schema.this.name

        columns: list[Column] = []
        for definition in schema.expressions:
            if isinstance(definition, exp.ColumnDef):
                columns.append(Column(name=definition.name, inferred_type=self._type_of(definition)))
                inline = self._inline_foreign_key(table_name, definition)
                if inline is not None:
                    foreign_keys.append(inline)
            elif isinstance(definition, exp.ForeignKey):
                link = self._foreign_key(table_name, definition)
                if link is not None:
                    foreign_keys.append(link)

        return Table(name=table_name, columns=columns)

    def _type_of(self, definition: exp.ColumnDef) -> str:
        kind = definition.args.get("kind")
        return kind.sql(dialect=DIALECT) if kind else UNKNOWN_TYPE

    def _foreign_key(self, from_table: str, definition: exp.ForeignKey) -> Relationship | None:
        """Read ``FOREIGN KEY (a) REFERENCES t (b)``, declared on the table."""
        target = self._reference_target(definition.args.get("reference"))
        if target is None:
            return None

        to_table, to_column = target
        from_column = COLUMN_SEPARATOR.join(column.name for column in definition.expressions)
        name, inverse_name = foreign_key_names(from_column, to_table)
        return Relationship(
            from_table=from_table,
            to_table=to_table,
            type="FOREIGN_KEY",
            from_column=from_column,
            to_column=to_column,
            name=name,
            inverse_name=inverse_name,
        )

    def _inline_foreign_key(self, from_table: str, definition: exp.ColumnDef) -> Relationship | None:
        """Read ``a INT REFERENCES t (b)``, declared on the column itself.

        This form carries no ``FOREIGN KEY`` node, so it has to be found among
        the column's constraints rather than among the table's definitions.
        """
        for constraint in definition.constraints:
            target = self._reference_target(constraint.kind)
            if target is None:
                continue

            to_table, to_column = target
            name, inverse_name = foreign_key_names(definition.name, to_table)
            return Relationship(
                from_table=from_table,
                to_table=to_table,
                type="FOREIGN_KEY",
                from_column=definition.name,
                to_column=to_column,
                name=name,
                inverse_name=inverse_name,
            )
        return None

    def _reference_target(self, reference: exp.Expression | None) -> tuple[str, str] | None:
        """The table and columns a ``REFERENCES`` clause points at.

        The column list is optional in SQL — ``REFERENCES t`` means the target's
        primary key — so an empty string here is a valid answer, not a failure.
        """
        if not isinstance(reference, exp.Reference):
            return None

        target = reference.find(exp.Table)
        if target is None:
            return None

        schema = reference.find(exp.Schema)
        columns = [column.name for column in schema.expressions] if schema else []
        return target.name, COLUMN_SEPARATOR.join(columns)

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
            state.rows[table_name].append(dict(zip(column_names, literals)))

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
