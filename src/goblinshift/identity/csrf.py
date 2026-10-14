"""Verificação de CSRF, sem banco (docs/GoblinShiftPlan.md, 3.6; Frente B).

Nesta ordem, para toda mutação autenticada por cookie: Fetch Metadata (`Sec-Fetch-Site`
presente e diferente de `same-origin` recusa), `Origin` presente e diferente da origem pública
recusa, e o token *signed double-submit*: `X-CSRF-Token` igual ao cookie `__Host-gs_csrf`, cujo
valor é `aleatório ‖ HMAC-SHA256(chave, id da sessão ‖ aleatório)`, conferido em tempo
constante.

Contrato fixado na Fase 0 da fatia 6; os corpos são da Frente B.
"""

from uuid import UUID


def issue_csrf_token(key: bytes, session_id: UUID) -> str:
    raise NotImplementedError("Frente B da fatia 6")


def csrf_token_matches(
    key: bytes, session_id: UUID, cookie: str | None, header: str | None
) -> bool:
    raise NotImplementedError("Frente B da fatia 6")


def cross_site_request(sec_fetch_site: str | None, origin: str | None, public_origin: str) -> bool:
    """True quando Fetch Metadata ou `Origin` mostram que a requisição veio de outra origem."""
    raise NotImplementedError("Frente B da fatia 6")
