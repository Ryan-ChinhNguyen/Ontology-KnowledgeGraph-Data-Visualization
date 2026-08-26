"""Domain errors raised by the upload flow.

Each error names one rejection reason and owns its HTTP status, so services
raise a meaningful error and never assemble status codes inline.
"""

from fastapi import HTTPException, status


class ApiError(HTTPException):
    """Base for the service's errors. Subclasses set ``status_code`` once as a
    class attribute and pass only the message."""

    status_code: int = status.HTTP_400_BAD_REQUEST

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=self.status_code, detail=detail)


class NoFilesProvidedError(ApiError):
    def __init__(self) -> None:
        super().__init__("At least one file is required")


class TooManyFilesError(ApiError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"At most {limit} files may be uploaded at once")


class DuplicateFilenameError(ApiError):
    def __init__(self, filename: str) -> None:
        super().__init__(f"Duplicate filename in upload: '{filename}'")


class InvalidFilenameError(ApiError):
    def __init__(self, filename: str) -> None:
        super().__init__(f"Invalid filename: '{filename}'")


class InvalidFileExtensionError(ApiError):
    def __init__(self, extension: str) -> None:
        super().__init__(f"Unsupported file extension: '.{extension}'")


class MixedFormatsError(ApiError):
    def __init__(self) -> None:
        super().__init__("All files in one upload must share the same format")


class EmptyFileError(ApiError):
    def __init__(self, filename: str) -> None:
        super().__init__(f"File '{filename}' is empty")


class FileTooLargeError(ApiError):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE

    def __init__(self, limit_mb: int) -> None:
        super().__init__(f"Total upload size exceeds the {limit_mb}MB limit")


class DuplicateFileError(ApiError):
    status_code = status.HTTP_409_CONFLICT

    def __init__(self, filename: str) -> None:
        super().__init__(f"File '{filename}' has already been uploaded")


class UploadConflictError(ApiError):
    """A constraint rejected the upload and no more specific rule matched."""

    status_code = status.HTTP_409_CONFLICT

    def __init__(self) -> None:
        super().__init__("Upload conflicts with existing data")


class QueueUnavailableError(ApiError):
    """The upload was stored but could not be handed to the Worker."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    def __init__(self) -> None:
        super().__init__("Job queue is unavailable; the upload will be retried automatically")


class SessionNotFoundError(ApiError):
    status_code = status.HTTP_404_NOT_FOUND

    def __init__(self, session_id: str) -> None:
        super().__init__(f"Session '{session_id}' not found")
