"""Download por navegação que falha volta à tela da execução (64a, D6 a).

O link "Baixar" da interface navega até o download, sem o atributo `download`. Quando a API
recusa (401, 404, 410, 429…), o navegador mostraria o problem+json cru ou o gravaria com o nome
do arquivo. Por isso, só para essa navegação, a recusa vira um 303 para
`/execucoes/<uuid>?falha=<code>`, e a tela da execução mostra o motivo.

O redirecionamento só acontece quando tudo abaixo vale; em qualquer outro caso o chamador segue
com o problem+json de sempre:

- o caminho é o download de artefato;
- é navegação do navegador (`Sec-Fetch-Mode: navigate` ou `text/html` no `Accept`);
- o `Sec-Fetch-Site`, quando vem, é `same-origin` (um site de terceiro não forja o aviso);
- o caminho do `Referer` é, por inteiro, a tela de uma execução.

O `Location` é relativo e montado só com o UUID validado do `Referer`, então nenhum `Referer`
leva para fora da tela da execução. Este módulo não importa `problems.py` em execução (só a
anotação de `ProblemCode`): é ele quem importa este.
"""

import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import RedirectResponse, Response

if TYPE_CHECKING:
    # Só para a anotação: em execução, `problems.py` importa este módulo, e não o contrário.
    from goblinshift.entrypoints.api.problems import ProblemCode

_DOWNLOAD_PATH = re.compile(r"/api/v1/artifacts/[^/]+/download")
# UUID canônico: minúsculo, com hífens, como a interface monta a rota da execução.
_RUN_PAGE = re.compile(r"/execucoes/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def failure_redirect(request: Request, code: ProblemCode) -> Response | None:
    """303 de volta à tela da execução, ou `None` quando a recusa segue em problem+json."""
    if _DOWNLOAD_PATH.fullmatch(request.url.path) is None:
        return None
    if not _is_same_origin_navigation(request):
        return None
    run_id = _run_id_of(request.headers.get("referer", ""))
    if run_id is None:
        return None
    return RedirectResponse(
        f"/execucoes/{run_id}?falha={code.value}",
        status_code=303,
        headers={"Cache-Control": "no-store"},
    )


def _is_same_origin_navigation(request: Request) -> bool:
    site = request.headers.get("sec-fetch-site")
    if site is not None and site != "same-origin":
        return False
    if request.headers.get("sec-fetch-mode") == "navigate":
        return True
    return "text/html" in request.headers.get("accept", "")


def _run_id_of(referer: str) -> str | None:
    try:
        path = urlsplit(referer).path
    except ValueError:
        return None
    page = _RUN_PAGE.fullmatch(path)
    return None if page is None else page.group(1)
