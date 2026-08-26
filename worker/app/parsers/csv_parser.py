import pandas as pd

from app.parsers.dataframe import DataFrameParser

#: ``utf-8-sig`` strips the byte-order mark that Excel prepends when saving as
#: CSV on Windows, which would otherwise corrupt the first column's name.
CSV_ENCODING = "utf-8-sig"


class CsvParser(DataFrameParser):
    def __init__(self, delimiter: str = ",") -> None:
        self._delimiter = delimiter

    def read_frame(self, path: str) -> pd.DataFrame:
        return pd.read_csv(path, sep=self._delimiter, encoding=CSV_ENCODING)
