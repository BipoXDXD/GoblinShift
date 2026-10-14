"""Configuração lida do ambiente (prefixo `GS_`)."""

from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

MIN_SECRET_CHARS = 32
# Imagem do executor sempre por digest (contrato da fatia 4): `<repositório>@sha256:<64 hex>`.
EXECUTOR_IMAGE_PATTERN = r"^[a-z0-9]+(?:[._/:-][a-z0-9]+)*@sha256:[0-9a-f]{64}$"
# O Runner local roda o código do próprio repositório: a Run registra isto no lugar do digest.
LOCAL_EXECUTOR_IMAGE = "local-dev"
# Grupo `gs-exec` do host, que o executor (UID 61000) e o worker compartilham.
EXECUTOR_GROUP_ID = 61001
# Só para desenvolvimento e testes; em produção a ausência do segredo impede a API de subir.
_DEV_CURSOR_KEY = "goblinshift-dev-only-cursor-key-not-a-secret"


class MissingSecretError(RuntimeError):
    """Segredo obrigatório em produção não configurado."""


class ExecutorConfigError(RuntimeError):
    """A escolha do Runner ou da imagem do executor não serve a este ambiente."""


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

    # Execução (3.4 e 3.6). Em produção só o `executor_ctl` (container gVisor por tentativa); o
    # Runner local, em subprocesso sem sandbox, é de desenvolvimento e testes.
    runner: Literal["local", "executor_ctl"] = "local"
    # Imagem do executor que a aceitação fixa na Run e o worker manda no `start`. API, worker e
    # manutenção leem a mesma variável, exportada pelo deploy (deploy/release.sh).
    executor_image: str | None = Field(default=None, pattern=EXECUTOR_IMAGE_PATTERN)
    executor_ctl_socket: Path = Path("/run/goblinshift/executor-ctl.sock")
    executor_group_id: int = EXECUTOR_GROUP_ID
    # Raiz das áreas de trabalho das tentativas; com o `executor_ctl`, o filesystem de tamanho
    # fixo do host, montado no worker e na manutenção.
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

    def executor_image_for_runs(self) -> str:
        """Imagem que a Run fixa; recusa a combinação que não serve ao ambiente.

        Em produção só o `executor_ctl` existe, sem exceção (3.9, fatia 4): o Runner local não tem
        o isolamento do gVisor. Chamado na subida da API, do worker e da manutenção.
        """
        if self.environment == "production" and self.runner != "executor_ctl":
            raise ExecutorConfigError("produção exige GS_RUNNER=executor_ctl")
        if self.runner == "local":
            return LOCAL_EXECUTOR_IMAGE
        if self.executor_image is None:
            raise ExecutorConfigError("GS_RUNNER=executor_ctl exige GS_EXECUTOR_IMAGE")
        return self.executor_image

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
