"""The contract between the API (producer) and the Worker (consumer).

Queue names and the message body live here so a change to either side cannot
silently break the other.
"""

import uuid

from pydantic import BaseModel

JOB_QUEUE = "job_queue"
DEAD_QUEUE = "dead_queue"

DEFAULT_EXCHANGE = ""

#: Applied when declaring ``JOB_QUEUE``. Both services declare the queue on
#: startup, and RabbitMQ rejects a redeclaration whose arguments differ, so
#: the arguments must come from one place.
JOB_QUEUE_ARGUMENTS: dict[str, object] = {
    "x-dead-letter-exchange": DEFAULT_EXCHANGE,
    "x-dead-letter-routing-key": DEAD_QUEUE,
}


class JobMessage(BaseModel):
    """Body of a job message. Carries only identifiers — the Worker reads the
    session, its files, and the format from PostgreSQL, so the message stays
    small and never goes stale."""

    job_id: uuid.UUID
    session_id: uuid.UUID
    attempt: int = 0

    def encode(self) -> bytes:
        return self.model_dump_json().encode()

    @classmethod
    def decode(cls, body: bytes) -> "JobMessage":
        return cls.model_validate_json(body)
