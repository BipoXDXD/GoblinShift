"""Prazos das transferências no Nginx da imagem `web`, conferidos no template sem subir o Docker.

O envio passa pelo proxy sem buffer. Quando o `client_body_timeout` vence, o Nginx fecha a
conexão sem mandar resposta nenhuma (nem o `error_page 408` roda), então o 408 `REQUEST_TIMEOUT`
em problem+json do contrato só chega ao cliente se a API desistir primeiro: o Nginx repassa a
resposta dela mesmo com o corpo incompleto. Por isso o prazo do Nginx fica acima do prazo sem
progresso da API (1.7) e só cobre uma API travada.
"""

import re
from datetime import timedelta

from goblinshift.files.limits import TRANSFER_IDLE_TIMEOUT
from tests.test_nginx_hsts import location_blocks

UPLOAD_CONTENT = r"~ ^/api/v1/uploads/[^/]+/content$"
# Folga do Nginx sobre a API: cobre a autenticação e a vaga, que a API resolve antes de ligar o
# próprio relógio, e a latência da rede interna.
PROXY_BODY_TIMEOUT_MARGIN = timedelta(seconds=5)
_SECONDS = re.compile(r"^client_body_timeout (\d+)s;$")


def location_directives(pattern: str) -> list[str]:
    """Diretivas da `location` declarada como `location <pattern> {`, com os snippets expandidos."""
    return location_blocks()[f"location {pattern}"]


def _client_body_timeout_s(directives: list[str]) -> int:
    (seconds,) = [int(m[1]) for line in directives if (m := _SECONDS.match(line))]
    return seconds


def test_envio_espera_o_corpo_um_pouco_mais_que_a_api() -> None:
    timeout_s = _client_body_timeout_s(location_directives(UPLOAD_CONTENT))

    assert timeout_s == 35  # 30 s da API + 5 s de folga
    assert timeout_s == (TRANSFER_IDLE_TIMEOUT + PROXY_BODY_TIMEOUT_MARGIN).total_seconds()
    assert timeout_s > TRANSFER_IDLE_TIMEOUT.total_seconds()


def test_envio_passa_pelo_proxy_sem_buffer() -> None:
    assert "proxy_request_buffering off;" in location_directives(UPLOAD_CONTENT)


DOWNLOAD = r"~ ^/api/v1/artifacts/[^/]+/download$"


def test_download_tem_location_propria_sem_buffer() -> None:
    directives = location_directives(DOWNLOAD)

    assert {
        "proxy_buffering off;",
        "proxy_max_temp_file_size 0;",
        "limit_req zone=api burst=40 nodelay;",
        "error_page 429 = @rate_limited;",
        "error_page 502 504 = @upstream_unavailable;",
    } <= set(directives)
    assert f"send_timeout {int(TRANSFER_IDLE_TIMEOUT.total_seconds())}s;" in directives
