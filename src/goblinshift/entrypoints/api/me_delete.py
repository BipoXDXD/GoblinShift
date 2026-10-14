"""`DELETE /me`: exclusão da conta e de todos os seus dados (1.6 e 1.8; Frente C).

Só sessão com autenticação recente (`RecentSessionPrincipal`) e CSRF; responde 202 com
`Clear-Site-Data`. `GET /me` fica em `me.py` (Frente D).
"""

from fastapi import APIRouter

router = APIRouter(tags=["me"])
