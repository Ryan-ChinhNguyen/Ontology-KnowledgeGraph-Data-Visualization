from datetime import datetime, timezone


def utc_now() -> datetime:
    """Timezone-aware current time.

    Used instead of ``datetime.utcnow`` (deprecated, and naive) so that every
    timestamp written to PostgreSQL carries an explicit UTC offset.
    """
    return datetime.now(timezone.utc)
