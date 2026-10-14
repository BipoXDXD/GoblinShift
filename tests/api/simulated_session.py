"""Sessão web simulada para os testes das rotas da Frente C (`/api-keys`, `DELETE /me`).

O login (Frente A) e o cookie de sessão (Frente B) não fazem parte destes testes: a credencial
entra trocando `authenticate` por um Principal de sessão escolhido pelo teste. CSRF e a recusa de
chave em rota de sessão ficam na matriz de autorização, com a autenticação real.
"""

from datetime import datetime, timedelta
from uuid import UUID, uuid7

from fastapi import FastAPI

from goblinshift.entrypoints.api.dependencies import authenticate, current_time
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode
from goblinshift.identity import Principal, Scope
from tests.identity.pending_fronts import install_route_stand_ins


class FakeSessions:
    """Quem o app vê como autenticado por sessão, e o relógio da API."""

    def __init__(self, app: FastAPI, now: datetime) -> None:
        self.now = now
        self._principal: Principal | None = None
        app.dependency_overrides[authenticate] = self._current
        app.dependency_overrides[current_time] = lambda: self.now
        install_route_stand_ins(app)

    def sign_in(self, owner_id: UUID, *, minutes_ago: float = 1) -> None:
        """Sessão da conta com login no provedor há `minutes_ago` minutos."""
        self._principal = Principal(
            owner_id=owner_id,
            scopes=frozenset(Scope),
            via="session",
            session_id=uuid7(),
            authenticated_at=self.now - timedelta(minutes=minutes_ago),
        )

    def _current(self) -> Principal:
        if self._principal is None:
            raise ApiProblem(ProblemCode.AUTHENTICATION_REQUIRED, "Sem sessão.")
        return self._principal
