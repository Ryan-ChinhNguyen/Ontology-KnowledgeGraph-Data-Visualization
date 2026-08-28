"""Maps a session's format to the parser that handles it."""

from ontology_shared.models import FileFormat

from app.errors import UnsupportedFormatError
from app.parsers.base import BaseParser
from app.parsers.csv_parser import CsvParser
from app.parsers.json_parser import JsonParser
from app.parsers.parquet_parser import ParquetParser
from app.parsers.sql_parser import SqlParser

_PARSERS: dict[FileFormat, BaseParser] = {
    FileFormat.csv: CsvParser(),
    FileFormat.json: JsonParser(),
    FileFormat.sql: SqlParser(),
    FileFormat.parquet: ParquetParser(),
}


def parser_for(file_format: FileFormat) -> BaseParser:
    """The parser for ``file_format``.

    Keyed by the enum rather than by string so an unhandled format is a type
    error at the call site instead of a lookup miss at runtime.
    """
    parser = _PARSERS.get(file_format)
    if parser is None:
        raise UnsupportedFormatError(f"No parser registered for format '{file_format.value}'")
    return parser
