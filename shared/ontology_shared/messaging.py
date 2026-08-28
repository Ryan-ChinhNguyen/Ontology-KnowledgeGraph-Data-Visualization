"""The contract between the API (producer) and the Worker (consumer).

Queue names, their arguments, and the message body live here so a change to
either side cannot silently break the other.

Retries back off exponentially through a single holding queue, with the delay
set on each message rather than on the queue:

    job_queue --(failed, wait 5s)---> job_retry --(expires)--> job_queue
    job_queue --(failed, wait 25s)--> job_retry --(expires)--> job_queue
    job_queue --(attempts spent)----> dead_queue

A queue can only dead-letter to one destination, so choosing the delay means
the consumer has to publish the retry itself rather than simply rejecting the
message. It publishes with confirms and acknowledges the original only once
the broker reports the retry safely stored, so a failure at any point leaves
the job queued rather than lost.

Note the trade-off of putting every delay in one queue: RabbitMQ only checks
expiry at the head, so a message waiting 25 seconds holds back a 5-second one
queued behind it. The alternative — a queue per delay — trades that for extra
queues to declare and watch. With a single worker and a prefetch of one this
queue rarely holds more than one message, and the cost when it does is a
retry running late rather than anything being lost.
"""

import uuid

from pydantic import BaseModel

JOB_QUEUE = "job_queue"
RETRY_QUEUE = "job_retry"
DEAD_QUEUE = "dead_queue"

DEFAULT_EXCHANGE = ""

#: Wait before each retry, growing five-fold. A transient fault — a database
#: restarting, a brief network drop — usually clears within the first step;
#: the later one stops a lasting fault from being hammered.
#:
#: One entry per retry, which is one fewer than the attempt limit: the final
#: attempt is not followed by a retry but by dead-lettering.
RETRY_DELAYS_SECONDS: tuple[int, ...] = (5, 25)

#: The holding queue has no consumer and no TTL of its own — each message
#: carries its own expiry. Once that expires the broker dead-letters it back
#: to the job queue.
RETRY_QUEUE_ARGUMENTS: dict[str, object] = {
    "x-dead-letter-exchange": DEFAULT_EXCHANGE,
    "x-dead-letter-routing-key": JOB_QUEUE,
}

#: Applied when declaring ``JOB_QUEUE``. Both services declare the queues on
#: startup, and RabbitMQ rejects a redeclaration whose arguments differ, so the
#: arguments must come from one place.
JOB_QUEUE_ARGUMENTS: dict[str, object] = {
    "x-dead-letter-exchange": DEFAULT_EXCHANGE,
    "x-dead-letter-routing-key": DEAD_QUEUE,
}


def retry_delay_for(attempt: int) -> int:
    """Seconds to wait before retrying, given the attempt that just failed.

    An attempt beyond the last configured delay reuses the longest one, so a
    raised attempt limit degrades to a constant wait rather than failing.
    """
    index = min(max(attempt, 1), len(RETRY_DELAYS_SECONDS)) - 1
    return RETRY_DELAYS_SECONDS[index]


class JobMessage(BaseModel):
    """Body of a job message. Carries only identifiers — the Worker reads the
    session, its files, and the format from PostgreSQL, so the message stays
    small and never goes stale.

    ``attempt`` counts deliveries already made. It travels in the body because
    the consumer republishes the message to choose a delay, and a republished
    message starts a fresh delivery history at the broker.
    """

    job_id: uuid.UUID
    session_id: uuid.UUID
    attempt: int = 0

    def next_attempt(self) -> "JobMessage":
        return self.model_copy(update={"attempt": self.attempt + 1})

    def encode(self) -> bytes:
        return self.model_dump_json().encode()

    @classmethod
    def decode(cls, body: bytes) -> "JobMessage":
        return cls.model_validate_json(body)
