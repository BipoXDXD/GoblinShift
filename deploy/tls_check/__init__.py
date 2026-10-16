"""Vencimento do certificado TLS da borda (rede de segurança da renovação automática do Caddy).

Só biblioteca padrão: roda no Python do runner do GitHub Actions, sem instalar nada.
"""

import socket
import ssl
from datetime import UTC, datetime, timedelta

# Abaixo disso o alarme toca: o Caddy renova com cerca de 30 dias de folga, então 14 dias
# restantes já significam semanas de renovação falhando.
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
