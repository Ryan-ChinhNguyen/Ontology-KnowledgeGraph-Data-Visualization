"""Failures the Worker can classify with certainty.

Retrying is the safe default: a job wrongly retried costs a few wasted
attempts, while a job wrongly abandoned is work lost for good. So only errors
raised by this service — where the cause is known exactly — are marked
permanent. Anything arriving from a library is left to the retry path, because
a parsing library and a failing disk can surface the same exception type.
"""


class PermanentJobError(Exception):
    """A failure that repeating the job cannot fix.

    Raised where the cause rules out success on any attempt: the input itself
    is unusable, or what the job refers to no longer exists.
    """


class FileContentError(PermanentJobError):
    """The file's contents cannot represent a table."""


class UnsupportedFormatError(PermanentJobError):
    """No parser is registered for the session's format."""


class JobNotFoundError(PermanentJobError):
    """The job record is gone, so there is nothing left to process."""
