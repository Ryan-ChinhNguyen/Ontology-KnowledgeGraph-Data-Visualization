from ontology_shared.config import BaseAppSettings


class WorkerSettings(BaseAppSettings):
    """Settings specific to the Worker service."""

    #: One unacknowledged message at a time. Parsing is CPU- and memory-bound,
    #: so buffering more would only let one worker hoard jobs that another
    #: instance could be running.
    prefetch_count: int = 1

    #: Seconds between reconnection attempts while RabbitMQ is unreachable.
    reconnect_interval: int = 5


settings = WorkerSettings()
