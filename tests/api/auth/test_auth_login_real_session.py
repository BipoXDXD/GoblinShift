"""Login ponta a ponta com a sessão real da Frente B (sem `StoredSessions`).

Fica em xfail estrito enquanto `start_session` for stub: quando a Frente B chegar, o teste passa,
o xfail estrito reprova a suíte e quem integrar apaga a marca (e, com ela, a necessidade de
`StoredSessions` nos outros testes do login).
"""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity import authenticate_session
from goblinshift.platform.db import unit_of_work
from tests.api.auth.support import accounts, login, open_signup
from tests.identity.fake_providers import FakeProviders

pytestmark = pytest.mark.integration


def test_login_completo_com_a_sessao_real(
    client: TestClient, fake: FakeProviders, factory: sessionmaker[Session], login_app: FastAPI
) -> None:
    open_signup(login_app)

    response = login(client, fake, "google")

    assert response.status_code == 303
    token = client.cookies["__Host-gs_session"]
    with unit_of_work(factory) as session:
        principal = authenticate_session(session, token, datetime.now(UTC))
    assert principal is not None
    assert principal.via == "session"
    assert principal.owner_id == accounts(factory)[0]["id"]
