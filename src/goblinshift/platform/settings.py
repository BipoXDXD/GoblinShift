"""Configuração lida do ambiente (prefixo `GS_`)."""

from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

MIN_SECRET_CHARS = 32
# Só para desenvolvimento e testes; em produção a ausência do segredo impede a API de subir.
_DEV_CURSOR_KEY = "goblinshift-dev-only-cursor-key-not-a-secret"


class MissingSecretError(RuntimeError):
    """Segredo obrigatório em produção não configurado."""


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

    # Raiz do storage de objetos (3.6): fora de qualquer raiz pública servida pelo Nginx. O
    # default serve ao desenvolvimento local e é ignorado pelo git.
    storage_root: Path = Path(".storage")

    # Execução (3.4 e 3.6). Só o Runner local em subprocesso existe até a fatia 4; o
    # `executor_ctl` chega com o sandbox real.
    runner: Literal["local", "executor_ctl"] = "local"
    # Trava provisória até a fatia 4: em produção o worker só sobe com o Runner local se esta
    # confirmação explícita existir, porque o subprocesso local não tem o isolamento do gVisor.
    allow_local_runner: bool = False
    # Raiz das áreas de trabalho das tentativas, uma por `gs-exec-{attempt_id}`.
    work_root: Path = Path(".work")
    # Lease da tentativa; o coordenador o renova a cada terço do prazo (3.4).
    worker_lease_s: float = 60.0
    # Garantia contra NOTIFY perdido: o worker consulta a fila pelo menos nesse intervalo.
    worker_poll_interval_s: float = 2.0
    # Teto de cada tentativa (1.7, "Duração").
    attempt_timeout_s: float = 600.0
    maintenance_interval_s: float = 30.0
    # Lotes da manutenção toleram consultas mais longas que a API (3.2).
    maintenance_statement_timeout_ms: int = 30_000

    # Chave HMAC dos cursores da listagem (1.6). Trocá-la invalida os cursores emitidos, o que
    # só custa ao cliente recomeçar a paginação.
    cursor_secret: SecretStr | None = Field(default=None, min_length=MIN_SECRET_CHARS)

    def cursor_signing_key(self) -> bytes:
        if self.cursor_secret is not None:
            return self.cursor_secret.get_secret_value().encode()
        if self.environment == "production":
            raise MissingSecretError("GS_CURSOR_SECRET é obrigatório em produção")
        return _DEV_CURSOR_KEY.encode()

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
