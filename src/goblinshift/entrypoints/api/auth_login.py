"""`GET /auth/login/{provider}` e `GET /auth/callback/{provider}` (3.6; Frente A).

Fora de `/api/v1` e do OpenAPI. Rotas públicas: estão na allowlist da matriz de autorização.
O `redirect_uri` sai de `WebIdentityConfig.public_origin`, nunca do header `Host`.
"""

from fastapi import APIRouter, FastAPI

from goblinshift.platform.settings import WebIdentityConfig

router = APIRouter(prefix="/auth", include_in_schema=False)


def install(app: FastAPI, web: WebIdentityConfig) -> None:
    """Registra os provedores (Authlib) em `app.state`; chamado pelo `create_app`.

    Até a Frente A, nenhum provedor é registrado e o router não tem rotas.
    """
