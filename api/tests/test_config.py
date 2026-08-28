"""Guards on how credentials reach a connection string.

A password is arbitrary text. Formatting one into a URL without escaping makes
the URL parse wrongly — a `@` ends the credentials early and the rest of the
password is read as the hostname, which surfaces as a DNS failure rather than
as an authentication error.
"""

from urllib.parse import unquote, urlsplit

import pytest
from ontology_shared.config import BaseAppSettings

AWKWARD_PASSWORDS = [
    "12345678x@X",
    "p@ss:word",
    "sl/ash",
    "per%cent",
    "hash#tag",
    "quest?ion",
    "every@:/?#%thing",
]


def settings_with(password: str) -> BaseAppSettings:
    return BaseAppSettings(
        postgres_user="postgres",
        postgres_password=password,
        postgres_db="postgres",
        postgres_host="localhost",
        postgres_port=5432,
        rabbitmq_user="guest",
        rabbitmq_password=password,
        rabbitmq_host="localhost",
        rabbitmq_port=5672,
    )


class TestDatabaseUrl:
    @pytest.mark.parametrize("password", AWKWARD_PASSWORDS)
    def test_host_survives_a_password_with_special_characters(self, password: str) -> None:
        parts = urlsplit(settings_with(password).database_url)

        assert parts.hostname == "localhost"
        assert parts.port == 5432

    @pytest.mark.parametrize("password", AWKWARD_PASSWORDS)
    def test_password_round_trips(self, password: str) -> None:
        """``urlsplit`` hands back the still-encoded value, so decoding it is
        what proves the escaping is reversible rather than lossy."""
        encoded = urlsplit(settings_with(password).database_url).password
        assert unquote(encoded) == password

    def test_database_and_driver_are_kept(self) -> None:
        url = settings_with("plain").database_url

        assert url.startswith("postgresql+asyncpg://")
        assert urlsplit(url).path == "/postgres"


class TestRabbitMqUrl:
    @pytest.mark.parametrize("password", AWKWARD_PASSWORDS)
    def test_host_survives_a_password_with_special_characters(self, password: str) -> None:
        parts = urlsplit(settings_with(password).rabbitmq_url)

        assert parts.hostname == "localhost"
        assert parts.port == 5672

    @pytest.mark.parametrize("password", AWKWARD_PASSWORDS)
    def test_password_round_trips(self, password: str) -> None:
        encoded = urlsplit(settings_with(password).rabbitmq_url).password
        assert unquote(encoded) == password
