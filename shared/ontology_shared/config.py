from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class BaseAppSettings(BaseSettings):
    """Settings both services need: where PostgreSQL, RabbitMQ, and the file
    store live. Each service subclasses this and adds its own fields."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    rabbitmq_user: str
    rabbitmq_password: str
    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = 5672

    upload_dir: str = "/app/uploads"
    max_retry_attempts: int = 3

    @property
    def database_url(self) -> str:
        """Connection string with credentials escaped.

        Built through ``URL.create`` rather than by formatting a string,
        because a password containing ``@``, ``:``, ``/`` or ``%`` would
        otherwise be read as part of the host and produce a DNS lookup for a
        name that does not exist.
        """
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)

    @property
    def rabbitmq_url(self) -> str:
        """Broker URL with credentials percent-encoded, for the same reason as
        ``database_url``."""
        username = quote(self.rabbitmq_user, safe="")
        password = quote(self.rabbitmq_password, safe="")
        return f"amqp://{username}:{password}@{self.rabbitmq_host}:{self.rabbitmq_port}/"
