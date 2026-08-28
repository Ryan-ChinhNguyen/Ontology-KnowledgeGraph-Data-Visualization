import pandas as pd

from app.parsers.dataframe import DataFrameParser


class ParquetParser(DataFrameParser):
    def read_frame(self, path: str) -> pd.DataFrame:
        return pd.read_parquet(path)
