"""Tier 0 upload rules.

These checks are pure and side-effect free: they look at filenames and sizes
only, never at file contents. Anything that requires reading a file belongs in
the Worker, which reports content problems case by case.
"""

from pathlib import PurePosixPath, PureWindowsPath

from fastapi import UploadFile
from ontology_shared.models import FileFormat

from app.core.config import settings
from app.exceptions import (
    DuplicateFilenameError,
    InvalidFileExtensionError,
    InvalidFilenameError,
    MixedFormatsError,
    NoFilesProvidedError,
    TooManyFilesError,
)

EXTENSION_FORMATS: dict[str, FileFormat] = {
    "csv": FileFormat.csv,
    "tsv": FileFormat.csv,
    "json": FileFormat.json,
    "sql": FileFormat.sql,
    "parquet": FileFormat.parquet,
}


def safe_filename(filename: str | None) -> str:
    """Reduce a client-supplied filename to a bare name.

    The multipart filename is attacker-controlled, so a value like
    ``../../etc/passwd`` must not be able to steer a write outside the upload
    directory. Both separators are stripped because a Windows client can send
    a backslash path to a POSIX server.
    """
    if not filename:
        raise InvalidFilenameError("")

    name = PureWindowsPath(PurePosixPath(filename).name).name
    if not name or name in {".", ".."} or name.startswith("."):
        raise InvalidFilenameError(filename)
    return name


def resolve_format(filename: str) -> FileFormat:
    """Map a filename's extension to the format its parser handles."""
    return _format_of(safe_filename(filename))


def _format_of(name: str) -> FileFormat:
    """As ``resolve_format``, for a name that is already sanitised."""
    _, separator, extension = name.rpartition(".")
    if not separator:
        raise InvalidFileExtensionError("")

    normalized = extension.lower()
    if normalized not in EXTENSION_FORMATS:
        raise InvalidFileExtensionError(normalized)
    return EXTENSION_FORMATS[normalized]


def validate_upload(files: list[UploadFile]) -> FileFormat:
    """Check the batch as a whole and return the format it will be parsed as.

    Raises the first rule violated; the checks run cheapest-first so an
    oversized or malformed batch is rejected before any name is inspected.
    """
    if not files:
        raise NoFilesProvidedError()

    if len(files) > settings.max_files_per_session:
        raise TooManyFilesError(settings.max_files_per_session)

    seen: set[str] = set()
    formats: set[FileFormat] = set()

    for file in files:
        name = safe_filename(file.filename)
        if name in seen:
            raise DuplicateFilenameError(name)
        seen.add(name)
        formats.add(_format_of(name))  # already sanitised, so skip re-checking it

    if len(formats) > 1:
        raise MixedFormatsError()

    return formats.pop()
