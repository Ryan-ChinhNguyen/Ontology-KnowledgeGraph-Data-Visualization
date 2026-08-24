from pathlib import Path

import pandas as pd

from app.parsers.base import BaseParser, Column, NormalizedData, Table


class CsvParser(BaseParser):
    def __init__(self, delimiter: str = ","):
        self.delimiter = delimiter

    def parse(self, file_paths: list[str]) -> NormalizedData:
        tables = []
        for path in file_paths:
            table_name = Path(path).stem
            df = pd.read_csv(path, sep=self.delimiter, encoding="utf-8-sig")

            columns = [
                Column(name=col, inferred_type=str(df[col].dtype))
                for col in df.columns
            ]
            rows = df.where(pd.notna(df), None).to_dict(orient="records")

            tables.append(Table(name=table_name, columns=columns, rows=rows))

        return NormalizedData(tables=tables)
