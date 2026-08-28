import json
from pathlib import Path
from typing import Any

from app.errors import FileContentError
from app.parsers.base import BaseParser, Column, NormalizedData, Table

NESTED_KEY_SEPARATOR = "."


class JsonParser(BaseParser):
    """Reads flat and nested JSON.

    Nested objects are flattened into dotted column names — ``{"user": {"id": 1}}``
    becomes the column ``user.id`` — which keeps the table rectangular. Deciding
    which of those flattened columns deserve to become entities of their own is
    the ontology stage's job, not the parser's.
    """

    def parse(self, file_paths: list[str]) -> NormalizedData:
        tables: list[Table] = []
        for path in file_paths:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
            tables.extend(self._to_tables(Path(path).stem, document))
        return NormalizedData(tables=tables)

    def _to_tables(self, default_name: str, document: Any) -> list[Table]:
        if isinstance(document, list):
            return [self._build_table(default_name, document)]

        if not isinstance(document, dict):
            raise FileContentError("JSON root must be an object or an array")

        # An object whose values are arrays holds several record sets, one per
        # key — the shape a multi-table export usually takes.
        record_sets = {key: value for key, value in document.items() if isinstance(value, list)}
        if record_sets:
            return [self._build_table(key, records) for key, records in record_sets.items()]

        return [self._build_table(default_name, [document])]

    def _build_table(self, name: str, records: list[Any]) -> Table:
        rows = [self._flatten(record) for record in records if isinstance(record, dict)]
        if not rows:
            return Table(name=name)

        return Table(name=name, columns=self._describe_columns(rows), rows=rows)

    def _describe_columns(self, rows: list[dict[str, Any]]) -> list[Column]:
        """Derive the column set and each column's type in one pass.

        Records may not share keys, so the column set is their union. Types are
        summarised as the set of value types seen, which keeps this to a single
        walk of the data rather than one re-scan per column. Insertion order is
        preserved so the output is stable across runs.
        """
        observed: dict[str, set[type]] = {}
        for row in rows:
            for key, value in row.items():
                types = observed.setdefault(key, set())
                if value is not None:
                    types.add(type(value))

        return [Column(name=key, inferred_type=_type_name(types)) for key, types in observed.items()]

    def _flatten(self, record: dict[str, Any]) -> dict[str, Any]:
        flattened: dict[str, Any] = {}
        self._collect(record, "", flattened)
        return flattened

    def _collect(self, record: dict[str, Any], prefix: str, into: dict[str, Any]) -> None:
        """Write a record's leaves into ``into`` under their dotted paths.

        Every level writes into the same dictionary. Returning one dictionary
        per level and merging upwards would instead copy each leaf once per
        level of nesting above it.
        """
        for key, value in record.items():
            qualified = f"{prefix}{NESTED_KEY_SEPARATOR}{key}" if prefix else key
            if isinstance(value, dict):
                self._collect(value, qualified, into)
            else:
                into[qualified] = value


def _type_name(types: set[type]) -> str:
    """Name the type covering every value seen in a column.

    Operates on the distinct types rather than the values, so the cost depends
    on how many types a column mixes — nearly always one — not on row count.
    """
    if not types:
        return "null"
    # bool is a subclass of int, so it has to be ruled out before the widening
    # checks below would silently absorb it.
    if types == {bool}:
        return "boolean"
    if types <= {bool, int}:
        return "integer"
    if types <= {bool, int, float}:
        return "float"
    if types == {list}:
        return "array"
    return "string"
