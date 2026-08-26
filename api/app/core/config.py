from ontology_shared.config import BaseAppSettings


class ApiSettings(BaseAppSettings):
    """Settings specific to the API service."""

    max_file_size_mb: int = 20
    max_files_per_session: int = 5

    #: TCP connections are expensive, so keep few of them; channels are cheap
    #: and are what concurrent requests actually contend for.
    rabbitmq_connection_pool_size: int = 2
    rabbitmq_channel_pool_size: int = 10

    @property
    def max_upload_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


settings = ApiSettings()
