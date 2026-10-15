"""Configuração lida do ambiente (prefixo `GS_`)."""

from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
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
            public_origin=self._public_origin(production),
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
        )

    def _public_origin(self, production: bool) -> str:
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


def _read_secret(name: str, path: Path) -> str:
    """Conteúdo do arquivo de segredo sem espaços nas pontas; ausente ou vazio impede a subida."""
    try:
        value = path.read_text().strip()
    except FileNotFoundError as error:
        raise MissingSecretError(f"{name} aponta para um arquivo que não existe") from error
    except PermissionError as error:
        # No host o arquivo fica com 644 num diretório 700: o container lê com outro UID.
        raise MissingSecretError(
            f"{name} aponta para um arquivo que a API não pode ler (no host, use modo 644)"
        ) from error
    except IsADirectoryError as error:
        raise MissingSecretError(f"{name} aponta para um diretório, não um arquivo") from error
    if not value:
        raise MissingSecretError(f"{name} aponta para um arquivo vazio")
    return value
