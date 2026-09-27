import csv
import warnings
from pathlib import Path

import pandas as pd
from pandas.errors import ParserWarning

from app.errors import FileContentError
from app.parsers.dataframe import DataFrameParser

#: ``utf-8-sig`` strips the byte-order mark that Excel prepends when saving as
#: CSV on Windows, which would otherwise corrupt the first column's name.
CSV_ENCODING = "utf-8-sig"

#: pandas names a column with no header ``Unnamed: N``.
UNNAMED_PREFIX = "Unnamed:"


class CsvParser(DataFrameParser):
    """Reads delimiter-separated text.

    Column names come from the first row: the format carries no schema, so a
    file without a header would silently promote its first record into one.
    The checks below reject the header problems that can be recognised for
    certain; see ``_check_header`` for the one that cannot.
    """

    def __init__(self, delimiter: str = ",") -> None:
        self._delimiter = delimiter

    def read_frame(self, path: str) -> pd.DataFrame:
        self._check_header(path, self._delimiter)

        try:
            # pandas reports a row whose field count disagrees with the header
            # as a warning and drops the surplus. Promoting it to an error is
            # what stops a malformed file from being read as a shorter, wrong
            # one; without this the loss is silent.
            with warnings.catch_warnings():
                warnings.simplefilter("error", ParserWarning)
                return pd.read_csv(
                    path,
                    sep=self._delimiter,
                    encoding=CSV_ENCODING,
                    # Without this, a first record holding more fields than the
                    # header makes pandas treat the surplus leading value as a
                    # row index, shifting every column along.
                    index_col=False,
                )
        except ParserWarning as mismatch:
            raise FileContentError(
                f"'{Path(path).name}' has rows whose column count differs from the header"
            ) from mismatch

    def _check_header(self, path: str, delimiter: str) -> None:
        """Reject header rows that cannot be valid.

        Reading only the first line keeps this cheap regardless of file size.

        What this cannot catch: a file whose first record simply looks like a
        header, such as ``1,alice``. Telling that from a header naming its
        columns "1" and "alice" is not decidable from the file alone — it
        needs the uploader to say whether a header is present, in the same way
        the delimiter will be declared.
        """
        with open(path, encoding=CSV_ENCODING, newline="") as handle:
            first_line = handle.readline()

        names = next(csv.reader([first_line], delimiter=delimiter), [])
        names = [name.strip() for name in names]

        if not names:
            raise FileContentError(f"'{Path(path).name}' has no header row")

        if any(not name or name.startswith(UNNAMED_PREFIX) for name in names):
            raise FileContentError(f"'{Path(path).name}' has a column with no name")

        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise FileContentError(
                f"'{Path(path).name}' repeats the column name(s): {', '.join(sorted(duplicates))}"
            )

        # A header made entirely of numbers is a record, not a set of column
        # names. A header mixing text with numbers — "region,2023,2024" — is
        # ordinary, so only the all-numeric case is rejected.
        if all(_is_number(name) for name in names):
            raise FileContentError(
                f"'{Path(path).name}' starts with data rather than a header row"
            )


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True
