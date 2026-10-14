"""Segredos do provisionamento, lidos de arquivos no formato `.env`.

Credencial e URLs de destino nunca passam pela linha de comando nem pelo ambiente do shell (que
acabam no histórico e no `ps`): ficam em arquivos legíveis só pelo dono, como o
`/srv/goblinshift/secrets/app.env` da VPS, que já traz as credenciais do OpenObserve.

As URLs de destino também são segredo: quem tem a URL de um webhook ou de um tópico do ntfy
publica no canal. Por isso nenhuma mensagem de erro as mostra.
"""

import stat
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

EMAIL = "OPENOBSERVE_ROOT_EMAIL"
PASSWORD = "OPENOBSERVE_ROOT_PASSWORD"  # noqa: S105 (nome da chave, não o valor)
URGENT_URL = "GS_ALERT_URGENT_URL"
WARNING_URL = "GS_ALERT_WARNING_URL"
HEARTBEAT_URL = "GS_ALERT_HEARTBEAT_URL"
_REQUIRED = (EMAIL, PASSWORD, URGENT_URL, HEARTBEAT_URL)
_QUOTES = ("'", '"')


class SettingsError(Exception):
    """Configuração ausente ou inválida; a mensagem nunca traz o valor de um segredo."""


@dataclass(frozen=True)
class ProvisionSettings:
    email: str
    password: str = field(repr=False)
    urgent_url: str = field(repr=False)
    warning_url: str = field(repr=False)
    heartbeat_url: str = field(repr=False)

    @classmethod
    def from_files(cls, paths: Iterable[Path]) -> "ProvisionSettings":
        """Soma os arquivos na ordem dada; numa chave repetida, vale o último."""
        values: dict[str, str] = {}
        for path in paths:
            values.update(_read_env_file(path))
        missing = [name for name in _REQUIRED if not values.get(name)]
        if missing:
            raise SettingsError(f"faltam nos arquivos de segredos: {', '.join(missing)}")
        urgent = _https_url(values, URGENT_URL)
        warning = _https_url(values, WARNING_URL) if values.get(WARNING_URL) else urgent
        return cls(
            email=values[EMAIL],
            password=values[PASSWORD],
            urgent_url=urgent,
            warning_url=warning,
            heartbeat_url=_https_url(values, HEARTBEAT_URL),
        )


def _read_env_file(path: Path) -> dict[str, str]:
    try:
        mode = path.stat().st_mode
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SettingsError(f"não foi possível ler {path}: {error.strerror}") from None
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SettingsError(f"{path} pode ser lido por outros usuários; rode chmod 600 {path}")
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or not name.strip():
            raise SettingsError(f"{path}, linha {number}: esperado NOME=valor")
        values[name.strip()] = _unquoted(value.strip())
    return values


def _unquoted(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTES:
        return value[1:-1]
    return value


def _https_url(values: dict[str, str], name: str) -> str:
    """URL pública do destino: HTTPS, para a URL (que é segredo) não trafegar em claro.

    O OpenObserve recusa por conta própria destinos em IP privado (ADR 013).
    """
    url = values[name]
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise SettingsError(f"{name} precisa ser uma URL HTTPS com host (valor omitido)")
    return url
