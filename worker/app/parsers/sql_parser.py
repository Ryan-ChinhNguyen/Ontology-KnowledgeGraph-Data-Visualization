import re

import sqlglot
import sqlglot.expressions as exp

from app.parsers.base import BaseParser, Column, NormalizedData, Relationship, Table


DANGEROUS_STATEMENTS = (exp.Drop, exp.Command, exp.Use, exp.Set)


class SqlParser(BaseParser):
    def parse(self, file_paths: list[str]) -> NormalizedData:
        tables: dict[str, Table] = {}
        relationships: list[Relationship] = []

        for path in file_paths:
            with open(path, encoding="utf-8") as f:
                sql = f.read()

            statements = sqlglot.parse(sql, dialect="postgres", error_level=sqlglot.ErrorLevel.WARN)

            for stmt in statements:
                if stmt is None or isinstance(stmt, DANGEROUS_STATEMENTS):
                    continue

                if isinstance(stmt, exp.Create) and isinstance(stmt.this, exp.Schema):
                    table_name, columns, fks = self._parse_create(stmt)
                    tables[table_name] = Table(name=table_name, columns=columns)
                    relationships.extend(fks)

                elif isinstance(stmt, exp.Insert):
                    table_name, rows = self._parse_insert(stmt)
                    if table_name in tables:
                        tables[table_name].rows.extend(rows)

        for rel in relationships:
            if rel.from_table in tables:
                tables[rel.from_table].relationships.append(rel)

        return NormalizedData(tables=list(tables.values()))

    def _parse_create(self, stmt: exp.Create) -> tuple[str, list[Column], list[Relationship]]:
        schema = stmt.this
        table_name = schema.this.name
        columns = []
        fks = []

        for col_def in schema.expressions:
            if isinstance(col_def, exp.ColumnDef):
                columns.append(Column(
                    name=col_def.name,
                    inferred_type=col_def.args.get("kind", "unknown").sql() if col_def.args.get("kind") else "unknown",
                ))
            elif isinstance(col_def, exp.ForeignKey):
                fk_cols = [c.name for c in col_def.find_all(exp.Column)]
                ref = col_def.args.get("reference")
                if ref:
                    ref_table = ref.find(exp.Table)
                    if ref_table:
                        fks.append(Relationship(
                            from_table=table_name,
                            to_table=ref_table.name,
                            type="FOREIGN_KEY",
                        ))

        return table_name, columns, fks

    def _parse_insert(self, stmt: exp.Insert) -> tuple[str, list[dict]]:
        table_name = stmt.this.name if stmt.this else "unknown"
        rows = []

        values_clause = stmt.args.get("expression")
        if isinstance(values_clause, exp.Values):
            cols = [c.name for c in stmt.args.get("this", exp.Schema()).expressions] if stmt.args.get("this") else []
            for tuple_expr in values_clause.expressions:
                vals = [v.this if hasattr(v, "this") else str(v) for v in tuple_expr.expressions]
                if cols:
                    rows.append(dict(zip(cols, vals)))
                else:
                    rows.append({str(i): v for i, v in enumerate(vals)})

        return table_name, rows
