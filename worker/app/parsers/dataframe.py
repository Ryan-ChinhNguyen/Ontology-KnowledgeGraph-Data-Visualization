"""Shared handling for the formats pandas reads directly.

CSV and Parquet differ only in how the bytes become a DataFrame, so that step
is all a subclass has to supply.
"""

from abc import abstractmethod
from pathlib import Path

import pandas as pd

from app.parsers.base import BaseParser, Column, NormalizedData, Table


def table_from_dataframe(name: str, frame: pd.DataFrame) -> Table:
    """Describe a DataFrame as a Table, keeping the frame itself.

    Missing values are left as pandas represents them. Parquet stores them as
    nulls, and whatever reads the file back decides how to present them.
    """
    columns = [Column(name=str(col), inferred_type=str(frame[col].dtype)) for col in frame.columns]
    return Table(name=name, columns=columns, frame=frame)


class DataFrameParser(BaseParser):
    @abstractmethod
    def read_frame(self, path: str) -> pd.DataFrame:
        """Load one file into a DataFrame."""

    def parse(self, file_paths: list[str]) -> NormalizedData:
        return NormalizedData(
            tables=[table_from_dataframe(Path(path).stem, self.read_frame(path)) for path in file_paths]
        )
