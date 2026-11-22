"""Prazos das transferências no Nginx da imagem `web`, conferidos no template sem subir o Docker.

O envio passa pelo proxy sem buffer, então o prazo de leitura do corpo no Nginx é o primeiro a ver
um cliente parado. Ele fica igual ao prazo sem progresso da API (1.7): mais longo, o Nginx
seguraria a conexão depois de a API já ter desistido; mais curto, cortaria um envio que a API
ainda aceita.
"""

from goblinshift.files.limits import TRANSFER_IDLE_TIMEOUT
from tests.test_nginx_hsts import location_blocks

UPLOAD_CONTENT = r"~ ^/api/v1/uploads/[^/]+/content$"


def location_directives(pattern: str) -> list[str]:
    """Diretivas da `location` declarada como `location <pattern> {`, com os snippets expandidos."""
    return location_blocks()[f"location {pattern}"]


def test_envio_tem_client_body_timeout_igual_a_inatividade_da_api() -> None:
    directives = location_directives(UPLOAD_CONTENT)

    assert f"client_body_timeout {int(TRANSFER_IDLE_TIMEOUT.total_seconds())}s;" in directives
    assert "proxy_request_buffering off;" in directives
