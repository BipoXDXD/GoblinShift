"""`POST /auth/logout` e `POST /auth/logout-all` (docs/GoblinShiftPlan.md, 3.6; Frente B).

Fora de `/api/v1` e do OpenAPI. CSRF exigido nas duas; `logout` sem sessão válida também
responde 204, porque o estado final é o mesmo.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/auth", include_in_schema=False)
