from unittest.mock import MagicMock

import pytest
from fastapi import UploadFile
from ontology_shared.models import FileFormat

from app.exceptions import (
    DuplicateFilenameError,
    InvalidFileExtensionError,
    InvalidFilenameError,
    MixedFormatsError,
    NoFilesProvidedError,
    TooManyFilesError,
)
from app.services.validation import resolve_format, safe_filename, validate_upload


def upload(filename: str) -> UploadFile:
    file = MagicMock(spec=UploadFile)
    file.filename = filename
    return file


class TestSafeFilename:
    @pytest.mark.parametrize(
        "given,expected",
        [
            ("data.csv", "data.csv"),
            ("../../etc/passwd.csv", "passwd.csv"),
            ("/absolute/path/data.csv", "data.csv"),
            (r"C:\Users\me\data.csv", "data.csv"),
            (r"..\..\windows\system32\data.csv", "data.csv"),
        ],
    )
    def test_strips_directory_components(self, given: str, expected: str) -> None:
        assert safe_filename(given) == expected

    @pytest.mark.parametrize("given", [None, "", ".", "..", ".hidden", "../.."])
    def test_rejects_names_that_are_not_files(self, given: str | None) -> None:
        with pytest.raises(InvalidFilenameError):
            safe_filename(given)


class TestResolveFormat:
    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("data.csv", FileFormat.csv),
            ("data.tsv", FileFormat.csv),
            ("DATA.CSV", FileFormat.csv),
            ("records.json", FileFormat.json),
            ("dump.sql", FileFormat.sql),
            ("events.parquet", FileFormat.parquet),
            ("archive.tar.csv", FileFormat.csv),
        ],
    )
    def test_maps_extension_to_format(self, filename: str, expected: FileFormat) -> None:
        assert resolve_format(filename) == expected

    @pytest.mark.parametrize("filename", ["notes.txt", "book.xlsx", "report.pdf", "noextension"])
    def test_rejects_unsupported_extension(self, filename: str) -> None:
        with pytest.raises(InvalidFileExtensionError):
            resolve_format(filename)


class TestValidateUpload:
    def test_returns_the_shared_format(self) -> None:
        files = [upload("a.csv"), upload("b.tsv")]
        assert validate_upload(files) == FileFormat.csv

    def test_accepts_a_single_file(self) -> None:
        assert validate_upload([upload("data.json")]) == FileFormat.json

    def test_rejects_empty_batch(self) -> None:
        with pytest.raises(NoFilesProvidedError):
            validate_upload([])

    def test_rejects_more_than_five_files(self) -> None:
        with pytest.raises(TooManyFilesError):
            validate_upload([upload(f"table{index}.csv") for index in range(6)])

    def test_accepts_exactly_five_files(self) -> None:
        assert validate_upload([upload(f"table{index}.csv") for index in range(5)]) == FileFormat.csv

    def test_rejects_duplicate_filenames(self) -> None:
        with pytest.raises(DuplicateFilenameError):
            validate_upload([upload("data.csv"), upload("data.csv")])

    def test_rejects_names_that_collide_after_sanitising(self) -> None:
        with pytest.raises(DuplicateFilenameError):
            validate_upload([upload("data.csv"), upload("../data.csv")])

    def test_rejects_mixed_formats(self) -> None:
        with pytest.raises(MixedFormatsError):
            validate_upload([upload("data.csv"), upload("data.json")])

    def test_treats_csv_and_tsv_as_one_format(self) -> None:
        validate_upload([upload("a.csv"), upload("b.tsv"), upload("c.csv")])
