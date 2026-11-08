"""Vencimento do certificado TLS que o público recebe (rede de segurança, seja quem for o emissor).

Só biblioteca padrão: roda no Python do runner do GitHub Actions, sem instalar nada.
"""

import socket
import ssl
from datetime import UTC, datetime, timedelta

# Abaixo disso o alarme toca: 14 dias dão tempo de agir antes do vencimento.
MIN_DAYS_LEFT = 14
CONNECT_TIMEOUT_SECONDS = 10


def days_remaining(not_after: datetime, now: datetime) -> int:
    """Dias inteiros até `not_after`, arredondados para baixo (negativo se já venceu)."""
    return (not_after - now) // timedelta(days=1)


def has_enough_margin(days_left: int) -> bool:
    return days_left >= MIN_DAYS_LEFT


def read_not_after(host: str, port: int, context: ssl.SSLContext) -> datetime:
    """Faz o handshake com SNI e devolve o `notAfter` do certificado folha.

    O `context` valida a cadeia e o hostname; qualquer falha (DNS, conexão, handshake, cadeia,
    hostname) sai como `OSError`.
    """
    with (
        socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_SECONDS) as raw,
        context.wrap_socket(raw, server_hostname=host) as tls,
    ):
        cert = tls.getpeercert()
    not_after = cert.get("notAfter") if cert else None
    if not isinstance(not_after, str):
        raise ssl.SSLError(f"{host}:{port} não apresentou um certificado com notAfter")
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), UTC)
