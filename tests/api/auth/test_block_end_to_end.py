"""Roteiro de abuso (docs/runbooks/abuso.md) de ponta a ponta, contra o PostgreSQL real.

A conta entra pelo login real (provedor falso, sessão e cookie reais), recebe uma chave pela CLI
e tem uma execução na fila. Depois do `gs-admin block`, pela API e pelo banco: a sessão e a chave
respondem 401, a execução está `canceled`, o login seguinte é recusado, a auditoria registra o
bloqueio e nenhuma credencial da conta continua aberta.

Os pedaços já têm testes próprios (`tests/identity/test_block.py`,
`tests/identity/test_admin_cli.py` e os callbacks de login); este prova que o comando que o
roteiro manda rodar fecha tudo junto.
"""

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.admin_cli import EXIT_OK, main
from goblinshift.files.storage import FileStorage
from goblinshift.identity.tables import api_key, audit_event, web_session
from goblinshift.platform.settings import Settings
from goblinshift.runs.lifecycle import Status
from goblinshift.runs.tables import run as run_table
from tests.api.auth.support import accounts, assert_login_failed, browser, login, open_signup
from tests.api.support import assert_problem
from tests.identity.fake_providers import FakeProviders
from tests.runs.worker_support import queue_run

pytestmark = pytest.mark.integration

ME = "/api/v1/me"


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    """O mesmo diretório que o `login_settings` dá à API (`tmp_path / "storage"`)."""
    return tmp_path / "storage"


@pytest.fixture(autouse=True)
def cli_environment(db_settings: Settings, storage_root: Path) -> Iterator[None]:
    """A CLI lê as mesmas variáveis GS_* que a API, como no container."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GS_DB_HOST", db_settings.db_host)
        mp.setenv("GS_DB_PORT", str(db_settings.db_port))
        mp.setenv("GS_DB_USER", db_settings.db_user)
        mp.setenv("GS_DB_PASSWORD", db_settings.db_password.get_secret_value())
        mp.setenv("GS_DB_NAME", db_settings.db_name)
        mp.setenv("GS_STORAGE_ROOT", str(storage_root))
        yield


def gs_admin(capsys: pytest.CaptureFixture[str], *args: str) -> str:
    """Roda o comando como o roteiro manda e devolve o stdout; falha se não sair com 0."""
    exit_code = main(list(args))
    captured = capsys.readouterr()
    assert exit_code == EXIT_OK, captured.err
    return captured.out.strip()


def run_status(factory: sessionmaker[Session], run_id: UUID) -> str:
    with factory() as session:
        status: str = session.execute(
            select(run_table.c.status).where(run_table.c.id == run_id)
        ).scalar_one()
    return status


def open_credentials(factory: sessionmaker[Session], user_id: UUID) -> tuple[int, int]:
    """Quantas chaves e sessões da conta continuam sem revogação."""
    with factory() as session:
        keys = session.execute(
            select(api_key.c.id).where(
                api_key.c.owner_id == user_id, api_key.c.revoked_at.is_(None)
            )
        ).all()
        sessions = session.execute(
            select(web_session.c.id).where(
                web_session.c.user_id == user_id, web_session.c.revoked_at.is_(None)
            )
        ).all()
    return len(keys), len(sessions)


def admin_events(factory: sessionmaker[Session], user_id: UUID) -> list[str]:
    with factory() as session:
        return list(
            session.execute(
                select(audit_event.c.event_type)
                .where(audit_event.c.user_id == user_id, audit_event.c.event_type.like("ACCOUNT_%"))
                .order_by(audit_event.c.id)
            ).scalars()
        )


def test_block_fecha_sessao_chave_execucao_e_login_da_conta(
    capsys: pytest.CaptureFixture[str],
    client: TestClient,
    fake: FakeProviders,
    factory: sessionmaker[Session],
    login_app: FastAPI,
    storage_root: Path,
) -> None:
    open_signup(login_app)
    login(client, fake, "google")
    user_id: UUID = accounts(factory)[0]["id"]
    token = gs_admin(capsys, "issue-key", "--user", str(user_id), "--scopes", "files:read")
    key_headers = {"Authorization": f"Bearer {token}"}
    run_id = queue_run(factory, FileStorage(storage_root), user_id)
    with browser(login_app) as api_client:
        # Antes do bloqueio as duas credenciais funcionam: o 401 de depois vem do bloqueio.
        assert client.get(ME).status_code == 200
        assert api_client.get(ME, headers=key_headers).status_code == 200

        gs_admin(capsys, "block", "--user", str(user_id))

        assert_problem(client.get(ME), 401, "AUTHENTICATION_REQUIRED")
        assert_problem(api_client.get(ME, headers=key_headers), 401, "AUTHENTICATION_REQUIRED")
    assert run_status(factory, run_id) == Status.CANCELED
    assert_login_failed(login(client, fake, "google"))
    assert admin_events(factory, user_id) == ["ACCOUNT_BLOCKED"]
    assert open_credentials(factory, user_id) == (0, 0)
