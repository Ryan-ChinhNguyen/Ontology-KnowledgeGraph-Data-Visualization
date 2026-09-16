from ontology_shared.config import BaseAppSettings


class WorkerSettings(BaseAppSettings):
    """Settings specific to the Worker service."""

    #: One unacknowledged message at a time. Parsing is CPU- and memory-bound,
    #: so buffering more would only let one worker hoard jobs that another
    #: instance could be running.
    prefetch_count: int = 1

    #: Seconds between reconnection attempts while RabbitMQ is unreachable.
    reconnect_interval: int = 5

    #: How long a worker's claim on a job lasts before another worker may take
    #: the job over. It is what recovers a job whose worker died mid-parse, so
    #: it must comfortably exceed the longest parse: a lease that runs out
    #: while the job is still being worked on lets it be started twice. It
    #: also has to stay below RabbitMQ's consumer acknowledgement timeout.
    job_lease_seconds: int = 600

    #: Wait before looking again at a job that another worker currently holds.
    in_progress_recheck_seconds: int = 30

    #: How often the worker confirms it is still subscribed to the job queue.
    #: RabbitMQ drops a subscription without notice when the queue is deleted,
    #: and the client library only reinstates subscriptions after a reconnect.
    subscription_check_seconds: int = 10


settings = WorkerSettings()
