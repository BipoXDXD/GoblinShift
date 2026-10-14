"""`GET`, `POST /api-keys` e `DELETE /api-keys/{key_id}`: chaves pela sessão web (Frente C).

Só sessão (`SessionPrincipal`); emitir exige autenticação recente (`RecentSessionPrincipal`).
Ver docs/GoblinShiftPlan.md, 1.6 e 3.6.
"""

from fastapi import APIRouter

router = APIRouter(tags=["api-keys"])
