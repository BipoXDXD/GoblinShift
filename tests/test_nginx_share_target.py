"""Compartilhar do celular sem service worker (ADR 064): o Nginx não guarda o arquivo.

O `share_target` do manifest manda os arquivos num `POST /compartilhar`, que o service worker
intercepta. Sem ele (primeira visita, navegador sem suporte, registro que falhou), o POST chega ao
Nginx: em vez do 405 do estático ou do 413 do corpo, a pessoa vai para a Oficina com o motivo.
O `/sw.js` tem nome fixo e sai sem cache, como o index.html, para um deploy valer na próxima
navegação. Os testes de contrato leem o template; o `-m nginx_image` sobe a imagem.
"""

import urllib.request
import uuid

import pytest

from tests import test_nginx_spa
from tests.test_nginx_hsts import location_blocks
from tests.test_nginx_spa import Response, Stack, _NoRedirect, _send, pytestmark_integration

# A mesma imagem e a mesma API falsa de tests/test_nginx_spa.py.
stack = test_nginx_spa.stack

# Uma diretiva do snippet dos cabeçalhos da SPA: `location_blocks` expande os `include`.
SPA_HEADERS = "add_header X-Content-Type-Options nosniff always;"
SHARE_UNAVAILABLE = "/?compartilhamento=indisponivel"


def test_compartilhar_sem_service_worker_redireciona() -> None:
    share = location_blocks()["location = /compartilhar"]
    fallback = location_blocks()["location @share_unavailable"]

    assert {
        "client_max_body_size 64k;",
        "error_page 405 413 = @share_unavailable;",
        "try_files /index.html =404;",
        "add_header Cache-Control no-cache always;",
        SPA_HEADERS,
    } <= set(share)
    assert {
        "absolute_redirect off;",
        f"return 303 {SHARE_UNAVAILABLE};",
        SPA_HEADERS,
    } <= set(fallback)


def test_service_worker_tem_nome_fixo_e_sai_sem_cache() -> None:
    assert {
        "try_files $uri =404;",
        "add_header Cache-Control no-cache always;",
        SPA_HEADERS,
    } <= set(location_blocks()["location = /sw.js"])


def _post_multipart(stack: Stack, path: str, size: int) -> Response:
    boundary = uuid.uuid4().hex
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="arquivos"; filename="grande.csv"\r\n'
            "Content-Type: text/csv\r\n\r\n"
        ).encode()
        + b"a" * size
        + f"\r\n--{boundary}--\r\n".encode()
    )
    request = urllib.request.Request(  # noqa: S310
        stack.base_url + path,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    return _send(urllib.request.build_opener(_NoRedirect), request)


class TestCompartilharSemServiceWorker:
    pytestmark = pytestmark_integration

    @pytest.mark.parametrize("size", [10, 1024 * 1024], ids=["pequeno", "1MiB"])
    def test_compartilhar_sem_service_worker_redireciona(self, stack: Stack, size: int) -> None:
        response = _post_multipart(stack, "/compartilhar", size)

        assert response.status == 303
        assert response.headers["location"] == SHARE_UNAVAILABLE

    def test_get_de_compartilhar_abre_a_spa(self, stack: Stack) -> None:
        response = stack.get("/compartilhar?id=abc")

        assert response.status == 200
        assert response.headers["cache-control"] == "no-cache"
        assert b'<div id="root">' in response.body

    def test_sw_js_sai_sem_cache(self, stack: Stack) -> None:
        response = stack.get("/sw.js")

        assert response.status == 200
        assert response.headers["cache-control"] == "no-cache"
        assert "javascript" in response.headers["content-type"]
