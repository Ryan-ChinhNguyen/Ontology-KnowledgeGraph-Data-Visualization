import json
from pathlib import Path

from app.parsers.base import BaseParser, Column, NormalizedData, Table


class JsonParser(BaseParser):
    def parse(self, file_paths: list[str]) -> NormalizedData:
        tables = []
        for path in file_paths:
            table_name = Path(path).stem
            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, list):
                        tables.append(self._parse_records(key, value))
                    else:
                        tables.append(self._parse_records(table_name, [data]))
                        break
            elif isinstance(data, list):
                tables.append(self._parse_records(table_name, data))

        return NormalizedData(tables=tables)

    def _parse_records(self, name: str, records: list) -> Table:
        if not records:
            return Table(name=name)

        flat_records = [self._flatten(r) for r in records]
        all_keys = {k for r in flat_records for k in r}
        columns = [Column(name=k, inferred_type=self._infer_type(flat_records, k)) for k in all_keys]

        return Table(name=name, columns=columns, rows=flat_records)

    def _flatten(self, obj: dict, prefix: str = "") -> dict:
        result = {}
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                result.update(self._flatten(v, key))
            else:
                result[key] = v
        return result

    def _infer_type(self, records: list[dict], key: str) -> str:
        values = [r.get(key) for r in records if r.get(key) is not None]
        if not values:
            return "null"
        if all(isinstance(v, bool) for v in values):
            return "boolean"
        if all(isinstance(v, int) for v in values):
            return "integer"
        if all(isinstance(v, float) for v in values):
            return "float"
        return "string"
