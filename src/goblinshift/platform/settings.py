"""Configuração lida do ambiente (prefixo `GS_`)."""

from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GS_", extra="ignore", frozen=True)

    environment: Literal["development", "test", "production"] = "development"
    service_version: str = "dev"

    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "goblinshift"
    db_user: str = "goblinshift"
    # Em produção a senha vem de um arquivo de segredo do Compose (GS_DB_PASSWORD_FILE),
    # nunca de variável de ambiente ou do repositório.
    db_password: SecretStr = SecretStr("goblinshift")
    db_password_file: Path | None = None

    # Timeouts de 3.2: defesa contra consultas presas e transações esquecidas.
    db_statement_timeout_ms: int = 5_000
    db_lock_timeout_ms: int = 2_000
    db_idle_in_transaction_timeout_ms: int = 30_000
    db_connect_timeout_s: int = 5
    db_pool_size: int = 10
    db_pool_max_overflow: int = 5
    db_pool_timeout_s: float = 5.0

    alembic_ini: Path = Path("alembic.ini")

    @cached_property
    def database_url(self) -> URL:
        password = (
            self.db_password_file.read_text().strip()
            if self.db_password_file is not None
            else self.db_password.get_secret_value()
        )
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )
