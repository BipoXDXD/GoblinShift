"""Sessão web simulada para os testes das rotas da Frente C (`/api-keys`, `DELETE /me`).

O login e o cookie de sessão não fazem parte destes testes: a credencial entra trocando
`authenticate` por um Principal de sessão escolhido pelo teste. Isso deixa o teste escolher a idade
do login e repetir um pedido depois que a própria sessão foi revogada (a corrida de dois pedidos
já autenticados). CSRF, a recusa de chave em rota de sessão e a sessão real ficam na matriz de
autorização e em `test_session_auth.py`.

Com `persisted=True`, o `session_id` é de uma `web_session` gravada: a emissão de chave reconfere a
sessão no banco, sob a trava da conta (#442, 49b).
"""

from datetime import datetime, timedelta
from uuid import UUID, uuid7

from fastapi import FastAPI

from goblinshift.entrypoints.api.dependencies import authenticate, current_time
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode
from goblinshift.identity import Principal, Scope
from tests.api.session_support import open_session


class SimulatedSessions:
    """Quem o app vê como autenticado por sessão, e o relógio da API."""

    def __init__(self, app: FastAPI, now: datetime, *, persisted: bool = False) -> None:
        self.now = now
        self._app = app
        self._persisted = persisted
        self._principal: Principal | None = None
        app.dependency_overrides[authenticate] = self._current
        app.dependency_overrides[current_time] = lambda: self.now

    def sign_in(self, owner_id: UUID, *, minutes_ago: float = 1) -> None:
        """Sessão da conta com login no provedor há `minutes_ago` minutos."""
        authenticated_at = self.now - timedelta(minutes=minutes_ago)
        session_id = (
            open_session(
                self._app, owner_id, authenticated_at=authenticated_at, now=self.now
            ).session_id
            if self._persisted
            else uuid7()
        )
        self._principal = Principal(
            owner_id=owner_id,
            scopes=frozenset(Scope),
            via="session",
            session_id=session_id,
            authenticated_at=authenticated_at,
        )

    @property
    def session_id(self) -> UUID:
        if self._principal is None or self._principal.session_id is None:
            raise AssertionError("nenhuma sessão: chame sign_in antes")
        return self._principal.session_id

    def _current(self) -> Principal:
        if self._principal is None:
            raise ApiProblem(ProblemCode.AUTHENTICATION_REQUIRED, "Sem sessão.")
        return self._principal
