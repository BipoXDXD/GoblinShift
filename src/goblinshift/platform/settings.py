"""Configuração lida do ambiente (prefixo `GS_`)."""

import logging
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

MIN_SECRET_CHARS = 32
# Código de saída de processo com configuração inválida: distingue "configuração" de falha comum.
EXIT_CONFIGURATION = 2
# Imagem do executor sempre por digest (contrato da fatia 4): `<repositório>@sha256:<64 hex>`.
EXECUTOR_IMAGE_PATTERN = r"^[a-z0-9]+(?:[._/:-][a-z0-9]+)*@sha256:[0-9a-f]{64}$"
# O Runner local roda o código do próprio repositório: a Run registra isto no lugar do digest.
LOCAL_EXECUTOR_IMAGE = "local-dev"
# Grupo `gs-exec` do host, que o executor (UID 61000) e o worker compartilham.
EXECUTOR_GROUP_ID = 61001
# Teto signed int32 aceito por `statement_timeout`, `lock_timeout` e
# `idle_in_transaction_session_timeout`.
PG_MAX_TIMEOUT_MS = 2_147_483_647
# Só para desenvolvimento e testes; em produção a ausência do segredo impede a API de subir.
_DEV_CURSOR_KEY = "goblinshift-dev-only-cursor-key-not-a-secret"
_DEV_USER_HASH_KEY = "goblinshift-dev-only-user-hash-key-not-a-secret"
_DEV_CSRF_KEY = "goblinshift-dev-only-csrf-key-not-a-secret"
_DEV_LOGIN_COOKIE_KEY = "goblinshift-dev-only-login-cookie-key-not-a-secret"
# Origem do Nginx local; em produção GS_PUBLIC_ORIGIN é obrigatória.
_DEV_PUBLIC_ORIGIN = "http://localhost"


class MissingSecretError(RuntimeError):
    """Segredo obrigatório em produção não configurado."""


class ExecutorConfigError(RuntimeError):
    """A escolha do Runner ou da imagem do executor não serve a este ambiente."""


class WebConfigError(RuntimeError):
    """Configuração do login e da sessão web incompleta ou imprópria para o ambiente."""


@dataclass(frozen=True)
class OAuthClient:
    """Cliente registrado num provedor de login (Google ou GitHub)."""

    client_id: str
    client_secret: str = field(repr=False)


@dataclass(frozen=True)
class WebIdentityConfig:
    """Tudo o que o login e a sessão web leem da configuração, já validado na subida.

    `public_origin` é a única fonte do `redirect_uri` e da checagem de `Origin` (3.6): nunca o
    header `Host`. Um provedor `None` está desligado, o que só acontece fora de produção.
    """

    public_origin: str
    signup_open: bool
    google: OAuthClient | None
    github: OAuthClient | None
    oauth_http_timeout_s: float
    # HMAC do token CSRF (double-submit assinado, ligado à sessão).
    csrf_key: bytes = field(repr=False)
    # Assina o cookie `__Secure-gs_login`, que leva só o marcador do estado do login (ADR 025).
    login_cookie_key: bytes = field(repr=False)
    # Chave anterior do CSRF, só durante a troca do segredo (ADR 025); fora dela, None.
    csrf_previous_key: bytes | None = field(default=None, repr=False)


class Settings(BaseSettings):
    # `hide_input_in_errors=True` omite a entrada em `str`/`repr` (e `describe_config_error`
    # usa `include_input=False` em `errors()`); `nan`/`inf` nunca são válidos (#442, 54 a e b).
    model_config = SettingsConfigDict(
        env_prefix="GS_",
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
        allow_inf_nan=False,
    )

    environment: Literal["development", "test", "production"] = "development"
    service_version: str = "dev"

    db_host: str = "localhost"
    db_port: int = Field(default=5432, gt=0, le=65535)
    db_name: str = "goblinshift"
    db_user: str = "goblinshift"
    # Em produção a senha vem só de um arquivo de segredo do Compose (GS_DB_PASSWORD_FILE),
    # nunca de variável de ambiente ou do repositório. Este padrão é da pilha local e dos testes.
    db_password: SecretStr = SecretStr("goblinshift")
    db_password_file: Path | None = None

    # Timeouts de 3.2: defesa contra consultas presas e transações esquecidas.
    db_statement_timeout_ms: int = Field(default=5_000, gt=0, le=PG_MAX_TIMEOUT_MS)
    db_lock_timeout_ms: int = Field(default=2_000, gt=0, le=PG_MAX_TIMEOUT_MS)
    db_idle_in_transaction_timeout_ms: int = Field(default=30_000, gt=0, le=PG_MAX_TIMEOUT_MS)
    db_connect_timeout_s: int = Field(default=5, gt=0)
    db_pool_size: int = Field(default=10, gt=0)
    db_pool_max_overflow: int = Field(default=5, ge=0)
    db_pool_timeout_s: float = Field(default=5.0, gt=0)

    alembic_ini: Path = Path("alembic.ini")

    # Raiz do storage de objetos (3.6): fora de qualquer raiz pública servida pelo Nginx. O
    # default serve ao desenvolvimento local e é ignorado pelo git.
    storage_root: Path = Path(".storage")
    # Limitador da varredura de objetos ausentes (files/missing.py): acima desta fração do lote
    # ausente, nada é marcado `missing`. 1.0 desliga, só depois de um restore proposital (3.8).
    # O default é `files.missing.DEFAULT_MAX_ABSENT_RATIO` (platform não importa files).
    missing_sweep_max_absent_ratio: float = Field(default=0.5, gt=0, le=1)

    # Execução (3.4 e 3.6). Em produção só o `executor_ctl` (container gVisor por tentativa); o
    # Runner local, em subprocesso sem sandbox, é de desenvolvimento e testes.
    runner: Literal["local", "executor_ctl"] = "local"
    # Imagem do executor que a aceitação fixa na Run e o worker manda no `start`. API, worker e
    # manutenção leem a mesma variável, definida pela release do Coolify
    # (deploy/coolify/release/receiver.py).
    executor_image: str | None = Field(default=None, pattern=EXECUTOR_IMAGE_PATTERN)
    executor_ctl_socket: Path = Path("/run/goblinshift/executor-ctl.sock")
    executor_group_id: int = Field(default=EXECUTOR_GROUP_ID, gt=0)
    # Raiz das áreas de trabalho das tentativas; com o `executor_ctl`, o filesystem de tamanho
    # fixo do host, montado no worker e na manutenção.
    work_root: Path = Path(".work")
    # Lease da tentativa; o coordenador o renova a cada terço do prazo (3.4).
    worker_lease_s: float = Field(default=60.0, gt=0)
    # Garantia contra NOTIFY perdido: o worker consulta a fila pelo menos nesse intervalo.
    worker_poll_interval_s: float = Field(default=2.0, gt=0)
    # Teto de cada tentativa (1.7, "Duração").
    attempt_timeout_s: float = Field(default=600.0, gt=0)
    maintenance_interval_s: float = Field(default=30.0, gt=0)
    # Cotas diárias por conta (1.7): o padrão de toda conta, que `gs-admin set-limits` sobrepõe
    # conta a conta. Os tetos de armazenamento ficam no livro-razão (ADR 024).
    quota_runs_per_24h: int = Field(default=30, gt=0)
    quota_input_bytes_per_24h: int = Field(default=2 * 1024**3, gt=0)
    # Lotes da manutenção toleram consultas mais longas que a API (3.2).
    maintenance_statement_timeout_ms: int = Field(default=30_000, gt=0, le=PG_MAX_TIMEOUT_MS)

    # Chave HMAC dos cursores da listagem (1.6). Trocá-la invalida os cursores emitidos, o que
    # só custa ao cliente recomeçar a paginação.
    cursor_secret: SecretStr | None = Field(default=None, min_length=MIN_SECRET_CHARS)

    # Chave HMAC do `user.hash` dos eventos amplos (3.7). Trocá-la só quebra a continuidade do
    # agrupamento por conta na telemetria, que guarda 30 dias.
    user_hash_secret: SecretStr | None = Field(default=None, min_length=MIN_SECRET_CHARS)

    # Identidade web (fatia 6, 3.6). IDs de cliente não são segredo; os segredos vêm de arquivos
    # montados pelo Compose (`*_file`), no mesmo padrão de `db_password_file`.
    public_origin: str | None = None
    # Fechado até a abertura pública (fatia 8): identidade nova nasce bloqueada (ADR 023).
    signup_mode: Literal["closed", "open"] = "closed"
    google_client_id: str | None = None
    google_client_secret_file: Path | None = None
    github_client_id: str | None = None
    github_client_secret_file: Path | None = None
    csrf_secret_file: Path | None = None
    # Só durante a troca do segredo do CSRF, por até 8 h, e opcional também em produção (ADR 025).
    csrf_secret_previous_file: Path | None = None
    login_cookie_secret_file: Path | None = None
    # Teto de cada chamada ao Google e ao GitHub (3.2, "Timeouts em toda integração").
    oauth_http_timeout_s: float = Field(default=5.0, gt=0)

    @field_validator("public_origin")
    @classmethod
    def _origin_only(cls, value: str | None) -> str | None:
        """Esquema e host (com porta opcional), sem caminho, barra final, query nem usuário."""
        if value is None:
            return None
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.path
            or parts.query
            or parts.fragment
            or value.endswith(("?", "#"))
        ):
            raise ValueError("use só esquema e host, como https://goblinshift.dev")
        return value

    def web_identity(self) -> WebIdentityConfig:
        """Lê e valida a configuração do login; chamado na subida da API."""
        production = self.environment == "production"
        return WebIdentityConfig(
            public_origin=self.checked_public_origin(),
            signup_open=self.signup_mode == "open",
            google=self._oauth_client(
                production, "GOOGLE", self.google_client_id, self.google_client_secret_file
            ),
            github=self._oauth_client(
                production, "GITHUB", self.github_client_id, self.github_client_secret_file
            ),
            oauth_http_timeout_s=self.oauth_http_timeout_s,
            csrf_key=self._server_key(
                production, "GS_CSRF_SECRET_FILE", self.csrf_secret_file, _DEV_CSRF_KEY
            ),
            login_cookie_key=self._server_key(
                production,
                "GS_LOGIN_COOKIE_SECRET_FILE",
                self.login_cookie_secret_file,
                _DEV_LOGIN_COOKIE_KEY,
            ),
            csrf_previous_key=self._previous_csrf_key(production),
        )

    def _previous_csrf_key(self, production: bool) -> bytes | None:
        """Chave anterior do CSRF, com as regras da atual; sem o arquivo, None em todo ambiente."""
        if self.csrf_secret_previous_file is None:
            return None
        # Com o arquivo, `_server_key` só o lê e confere o tamanho: o padrão de dev nunca vale.
        return self._server_key(
            production, "GS_CSRF_SECRET_PREVIOUS_FILE", self.csrf_secret_previous_file, ""
        )

    def checked_public_origin(self) -> str:
        """A origem pública validada: a do login e a da URL nos eventos da API (3.6, 3.7)."""
        production = self.environment == "production"
        if self.public_origin is None:
            if production:
                raise WebConfigError("GS_PUBLIC_ORIGIN é obrigatória em produção")
            return _DEV_PUBLIC_ORIGIN
        # Cookies `__Host-`/`Secure` e os redirects dos provedores exigem HTTPS.
        if production and not self.public_origin.startswith("https://"):
            raise WebConfigError("GS_PUBLIC_ORIGIN precisa ser https em produção")
        return self.public_origin

    @staticmethod
    def _oauth_client(
        production: bool, provider: str, client_id: str | None, secret_file: Path | None
    ) -> OAuthClient | None:
        id_name = f"GS_{provider}_CLIENT_ID"
        secret_name = f"GS_{provider}_CLIENT_SECRET_FILE"
        if client_id is None and secret_file is None and not production:
            return None
        if not client_id:
            raise WebConfigError(f"{id_name} é obrigatório com o provedor ligado e em produção")
        if secret_file is None:
            if production:
                raise MissingSecretError(f"{secret_name} é obrigatório em produção")
            raise WebConfigError(f"{id_name} sem {secret_name}: configure os dois ou nenhum")
        return OAuthClient(
            client_id=client_id, client_secret=_read_secret(secret_name, secret_file)
        )

    @staticmethod
    def _server_key(production: bool, name: str, secret_file: Path | None, dev: str) -> bytes:
        if secret_file is None:
            if production:
                raise MissingSecretError(f"{name} é obrigatório em produção")
            return dev.encode()
        key = _read_secret(name, secret_file)
        if len(key) < MIN_SECRET_CHARS:
            raise WebConfigError(f"{name} precisa de pelo menos {MIN_SECRET_CHARS} caracteres")
        return key.encode()

    def cursor_signing_key(self) -> bytes:
        return self._secret_or_dev_default(self.cursor_secret, "GS_CURSOR_SECRET", _DEV_CURSOR_KEY)

    def user_hash_key(self) -> bytes:
        return self._secret_or_dev_default(
            self.user_hash_secret, "GS_USER_HASH_SECRET", _DEV_USER_HASH_KEY
        )

    def _secret_or_dev_default(self, secret: SecretStr | None, name: str, dev: str) -> bytes:
        if secret is not None:
            return secret.get_secret_value().encode()
        if self.environment == "production":
            raise MissingSecretError(f"{name} é obrigatório em produção")
        return dev.encode()

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

    def database_password(self) -> str:
        """Senha do banco; em produção, só do arquivo, cuja falta impede a subida do processo."""
        if self.db_password_file is not None:
            return _read_secret("GS_DB_PASSWORD_FILE", self.db_password_file)
        if self.environment == "production":
            raise MissingSecretError("GS_DB_PASSWORD_FILE é obrigatório em produção")
        return self.db_password.get_secret_value()

    @cached_property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.database_password(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


def _read_secret(name: str, path: Path) -> str:
    """Conteúdo do arquivo de segredo sem espaços nas pontas; ausente ou vazio impede a subida."""
    try:
        value = path.read_text().strip()
    except FileNotFoundError as error:
        raise MissingSecretError(f"{name} aponta para um arquivo que não existe") from error
    except PermissionError as error:
        # No host o arquivo fica com 644 num diretório 700: o container lê com outro UID.
        raise MissingSecretError(
            f"{name} aponta para um arquivo que o processo não pode ler (no host, use modo 644)"
        ) from error
    except IsADirectoryError as error:
        raise MissingSecretError(f"{name} aponta para um diretório, não um arquivo") from error
    if not value:
        raise MissingSecretError(f"{name} aponta para um arquivo vazio")
    return value


def describe_config_error(error: ValidationError) -> list[str]:
    """`<campo>:<tipo>` de cada erro, nunca o valor: a linha vai ao log do contêiner."""
    return [
        f"{'.'.join(str(part) for part in detail['loc'])}:{detail['type']}"
        for detail in error.errors(include_input=False, include_url=False, include_context=False)
    ]


def load_settings_or_exit(logger: logging.Logger) -> Settings:
    """`Settings()` do ambiente; inválida, uma linha com campo e tipo e saída 2, sem traceback."""
    try:
        return Settings()
    except ValidationError as error:
        logger.error(
            "configuração inválida",
            extra={"fields": {"config_errors": describe_config_error(error)}},
        )
        raise SystemExit(EXIT_CONFIGURATION) from None
