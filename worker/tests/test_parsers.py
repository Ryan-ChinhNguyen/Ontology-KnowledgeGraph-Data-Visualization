import json
from pathlib import Path

import pandas as pd
import pytest
from ontology_shared.models import FileFormat

from app.errors import FileContentError
from app.parsers.csv_parser import CsvParser
from app.parsers.json_parser import JsonParser
from app.parsers.parquet_parser import ParquetParser
from app.parsers.registry import parser_for
from app.parsers.sql_parser import SqlParser


def write(directory: Path, name: str, text: str, encoding: str = "utf-8") -> str:
    path = directory / name
    path.write_text(text, encoding=encoding)
    return str(path)


class TestCsvParser:
    def test_reads_columns_and_rows(self, tmp_path: Path) -> None:
        path = write(tmp_path, "people.csv", "id,name\n1,alice\n2,bob\n")

        table = CsvParser().parse([path]).tables[0]

        assert table.name == "people"
        assert [column.name for column in table.columns] == ["id", "name"]
        assert table.rows == [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]

    def test_strips_the_excel_byte_order_mark(self, tmp_path: Path) -> None:
        path = write(tmp_path, "bom.csv", "id,name\n1,alice\n", encoding="utf-8-sig")

        table = CsvParser().parse([path]).tables[0]

        assert table.columns[0].name == "id"

    def test_honours_a_semicolon_delimiter(self, tmp_path: Path) -> None:
        path = write(tmp_path, "euro.csv", "id;name\n1;alice\n")

        table = CsvParser(delimiter=";").parse([path]).tables[0]

        assert [column.name for column in table.columns] == ["id", "name"]

    def test_turns_missing_values_into_none(self, tmp_path: Path) -> None:
        path = write(tmp_path, "gaps.csv", "id,name\n1,\n2,bob\n")

        table = CsvParser().parse([path]).tables[0]

        assert table.rows[0]["name"] is None

    def test_reads_every_file_into_its_own_table(self, tmp_path: Path) -> None:
        first = write(tmp_path, "customers.csv", "id\n1\n")
        second = write(tmp_path, "orders.csv", "id\n9\n")

        tables = CsvParser().parse([first, second]).tables

        assert [table.name for table in tables] == ["customers", "orders"]


class TestJsonParser:
    def test_reads_an_array_root(self, tmp_path: Path) -> None:
        path = write(tmp_path, "people.json", json.dumps([{"id": 1, "name": "alice"}]))

        table = JsonParser().parse([path]).tables[0]

        assert table.name == "people"
        assert table.rows == [{"id": 1, "name": "alice"}]

    def test_splits_an_object_root_into_one_table_per_array(self, tmp_path: Path) -> None:
        document = {"customers": [{"id": 1}], "orders": [{"id": 9}]}
        path = write(tmp_path, "export.json", json.dumps(document))

        tables = JsonParser().parse([path]).tables

        assert {table.name for table in tables} == {"customers", "orders"}

    def test_treats_an_object_without_arrays_as_a_single_row(self, tmp_path: Path) -> None:
        path = write(tmp_path, "config.json", json.dumps({"id": 1, "name": "alice"}))

        table = JsonParser().parse([path]).tables[0]

        assert table.name == "config"
        assert table.rows == [{"id": 1, "name": "alice"}]

    def test_flattens_nested_objects_into_dotted_columns(self, tmp_path: Path) -> None:
        document = [{"id": 1, "address": {"city": "Hanoi", "geo": {"lat": 21.0}}}]
        path = write(tmp_path, "nested.json", json.dumps(document))

        table = JsonParser().parse([path]).tables[0]

        assert table.rows[0] == {"id": 1, "address.city": "Hanoi", "address.geo.lat": 21.0}

    def test_unions_columns_across_records_with_different_keys(self, tmp_path: Path) -> None:
        path = write(tmp_path, "sparse.json", json.dumps([{"a": 1}, {"b": 2}]))

        table = JsonParser().parse([path]).tables[0]

        assert [column.name for column in table.columns] == ["a", "b"]

    @pytest.mark.parametrize(
        "value,expected",
        [(True, "boolean"), (1, "integer"), (1.5, "float"), (["x"], "array"), ("x", "string")],
    )
    def test_infers_column_types(self, tmp_path: Path, value: object, expected: str) -> None:
        path = write(tmp_path, "typed.json", json.dumps([{"field": value}]))

        table = JsonParser().parse([path]).tables[0]

        assert table.columns[0].inferred_type == expected

    def test_rejects_a_scalar_root(self, tmp_path: Path) -> None:
        path = write(tmp_path, "scalar.json", json.dumps(42))

        with pytest.raises(FileContentError, match="object or an array"):
            JsonParser().parse([path])


class TestParquetParser:
    def test_reads_columns_and_rows(self, tmp_path: Path) -> None:
        path = tmp_path / "events.parquet"
        pd.DataFrame({"id": [1, 2], "name": ["alice", "bob"]}).to_parquet(path)

        table = ParquetParser().parse([str(path)]).tables[0]

        assert table.name == "events"
        assert [column.name for column in table.columns] == ["id", "name"]
        assert table.rows == [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]


class TestSqlParser:
    def test_reads_columns_from_create_table(self, tmp_path: Path) -> None:
        path = write(
            tmp_path,
            "dump.sql",
            "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT);",
        )

        table = SqlParser().parse([path]).tables[0]

        assert table.name == "customers"
        assert [column.name for column in table.columns] == ["id", "name"]

    def test_reads_rows_from_insert(self, tmp_path: Path) -> None:
        path = write(
            tmp_path,
            "dump.sql",
            "CREATE TABLE customers (id INTEGER, name TEXT);\n"
            "INSERT INTO customers (id, name) VALUES (1, 'alice'), (2, 'bob');",
        )

        table = SqlParser().parse([path]).tables[0]

        assert table.rows == [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]

    def test_falls_back_to_declared_columns_when_insert_omits_them(self, tmp_path: Path) -> None:
        path = write(
            tmp_path,
            "dump.sql",
            "CREATE TABLE customers (id INTEGER, name TEXT);\n"
            "INSERT INTO customers VALUES (1, 'alice');\n"
            "INSERT INTO customers VALUES (2, 'bob');",
        )

        table = SqlParser().parse([path]).tables[0]

        assert table.rows == [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]

    def test_records_a_foreign_key_as_a_relationship(self, tmp_path: Path) -> None:
        path = write(
            tmp_path,
            "dump.sql",
            "CREATE TABLE customers (id INTEGER PRIMARY KEY);\n"
            "CREATE TABLE orders (id INTEGER, customer_id INTEGER, "
            "FOREIGN KEY (customer_id) REFERENCES customers(id));",
        )

        tables = {table.name: table for table in SqlParser().parse([path]).tables}

        assert tables["orders"].relationships[0].to_table == "customers"
        assert tables["orders"].relationships[0].type == "FOREIGN_KEY"

    def test_keeps_a_table_that_has_no_rows(self, tmp_path: Path) -> None:
        path = write(tmp_path, "dump.sql", "CREATE TABLE empty_table (id INTEGER, label TEXT);")

        table = SqlParser().parse([path]).tables[0]

        assert table.rows == []
        assert len(table.columns) == 2

    def test_ignores_destructive_statements(self, tmp_path: Path) -> None:
        path = write(
            tmp_path,
            "dump.sql",
            "DROP TABLE IF EXISTS customers;\n"
            "CREATE TABLE customers (id INTEGER);\n"
            "TRUNCATE customers;",
        )

        tables = SqlParser().parse([path]).tables

        assert [table.name for table in tables] == ["customers"]

    def test_reads_null_as_none(self, tmp_path: Path) -> None:
        path = write(
            tmp_path,
            "dump.sql",
            "CREATE TABLE customers (id INTEGER, name TEXT);\n"
            "INSERT INTO customers (id, name) VALUES (1, NULL);",
        )

        table = SqlParser().parse([path]).tables[0]

        assert table.rows == [{"id": 1, "name": None}]

    def test_skips_rows_for_a_table_that_was_never_declared(self, tmp_path: Path) -> None:
        path = write(tmp_path, "dump.sql", "INSERT INTO ghost (id) VALUES (1);")

        assert SqlParser().parse([path]).tables == []


class TestRegistry:
    @pytest.mark.parametrize(
        "file_format,expected",
        [
            (FileFormat.csv, CsvParser),
            (FileFormat.json, JsonParser),
            (FileFormat.sql, SqlParser),
            (FileFormat.parquet, ParquetParser),
        ],
    )
    def test_returns_the_parser_for_each_format(self, file_format: FileFormat, expected: type) -> None:
        assert isinstance(parser_for(file_format), expected)
