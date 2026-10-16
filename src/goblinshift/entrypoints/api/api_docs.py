"""Documentação interativa em `/docs` (docs/GoblinShiftPlan.md, 1.6 e 1.8).

O Swagger UI sai da própria origem, de uma cópia fixada (`swagger_ui/`, atualizada por
`scripts/update-swagger-ui.sh`), nunca de CDN. A página tem CSP própria: só script e estilo da
origem, sem inline (a inicialização é um arquivo), e o bundle e o CSS levam SRI. Bundle e CSS têm
a versão no nome e cache imutável; o inicializador, que é nosso, revalida a cada visita.
"""

import base64
import hashlib
from dataclasses import dataclass
from importlib.resources import files

from fastapi import APIRouter, Response

from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode

SWAGGER_UI_VERSION = "5.33.0"
DOCS_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
_ASSETS_PATH = "/docs/assets"
_IMMUTABLE = "public, max-age=31536000, immutable"
_REVALIDATE = "no-cache"
_JS = "application/javascript; charset=utf-8"
_CSS = "text/css; charset=utf-8"


@dataclass(frozen=True)
class _Asset:
    content: bytes
    media_type: str
    cache_control: str

    @property
    def integrity(self) -> str:
        return "sha384-" + base64.b64encode(hashlib.sha384(self.content).digest()).decode()


def _read(name: str) -> bytes:
    return files(__package__).joinpath("swagger_ui", name).read_bytes()


_BUNDLE = f"swagger-ui-bundle-{SWAGGER_UI_VERSION}.js"
_STYLE = f"swagger-ui-{SWAGGER_UI_VERSION}.css"
_INIT = "swagger-init.js"
# Allowlist: só estes nomes existem em /docs/assets/; qualquer outro é 404.
_ASSETS: dict[str, _Asset] = {
    _BUNDLE: _Asset(_read("swagger-ui-bundle.js"), _JS, _IMMUTABLE),
    _STYLE: _Asset(_read("swagger-ui.css"), _CSS, _IMMUTABLE),
    _INIT: _Asset(_read(_INIT), _JS, _REVALIDATE),
}

_HTML = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>GoblinShift · API</title>
<link rel="stylesheet" href="{_ASSETS_PATH}/{_STYLE}" integrity="{_ASSETS[_STYLE].integrity}">
</head>
<body>
<div id="swagger-ui"></div>
<script src="{_ASSETS_PATH}/{_BUNDLE}" integrity="{_ASSETS[_BUNDLE].integrity}"></script>
<script src="{_ASSETS_PATH}/{_INIT}"></script>
</body>
</html>
"""

router = APIRouter(include_in_schema=False)


@router.get("/docs")
def docs_page() -> Response:
    return Response(
        _HTML,
        media_type="text/html; charset=utf-8",
        headers={"Content-Security-Policy": DOCS_CSP},
    )


@router.get(f"{_ASSETS_PATH}/{{name}}")
def docs_asset(name: str) -> Response:
    asset = _ASSETS.get(name)
    if asset is None:
        raise ApiProblem(ProblemCode.NOT_FOUND, "Arquivo da documentação inexistente.")
    return Response(
        asset.content, media_type=asset.media_type, headers={"Cache-Control": asset.cache_control}
    )
