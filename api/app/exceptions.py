from fastapi import HTTPException, status


class InvalidFileExtensionError(HTTPException):
    def __init__(self, ext: str) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file extension: .{ext}",
        )


class EmptyFileError(HTTPException):
    def __init__(self, filename: str) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File '{filename}' is empty",
        )


class MixedFormatsError(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mixed file formats are not allowed",
        )


class DuplicateFilenameError(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Duplicate filenames in upload",
        )


class TooManyFilesError(HTTPException):
    def __init__(self, max_files: int) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Max {max_files} files per upload",
        )


class FileTooLargeError(HTTPException):
    def __init__(self, limit_mb: int) -> None:
        super().__init__(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Total file size exceeds {limit_mb}MB limit",
        )


class DuplicateFileError(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail="File with this content already exists",
        )


class SessionNotFoundError(HTTPException):
    def __init__(self, session_id: str) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found",
        )
