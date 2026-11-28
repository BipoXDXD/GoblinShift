"""O cursor da listagem vale só para a conta que o recebeu (docs/GoblinShiftPlan.md, 1.6)."""

import hashlib
import hmac
import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid7

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from goblinshift.entrypoints.api.app import create_app
from goblinshift.entrypoints.api.dependencies import get_session
from goblinshift.platform.settings import Settings
from goblinshift.runs.cursor import CursorScopeMismatchError, InvalidCursorError
from goblinshift.runs.queries import RunFilters, list_runs
from tests.api.simulated_session import SimulatedSessions
from tests.api.support import assert_problem
from tests.runs.test_cursor import b64url, unb64url

NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)
SECRET = b"k" * 32
NO_FILTERS = RunFilters(None, None, None, None)
RUNS_URL = "/api/v1/runs"


class _FixedRows:
    """Session que devolve as mesmas linhas a qualquer consulta, sem banco."""

    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    def begin(self) -> nullcontext[None]:
        return nullcontext()

    def execute(self, _statement: object) -> _FixedRows:
        return self

    def all(self) -> list[SimpleNamespace]:
        return self._rows


def _run_row(age: timedelta) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid7(),
        recipe_id="csv-janitor",
        recipe_version=1,
        status="succeeded",
        phase=None,
        attempt=1,
        processed=1,
        total=1,
        queue_ahead=0,
        created_at=NOW - age,
        queue_expires_at=NOW,
        deadline_at=None,
        error_code=None,
        parameters=None,
        preset_id=None,
        results_expire_at=None,
    )


def _session(rows: list[SimpleNamespace]) -> Session:
    return cast(Session, _FixedRows(rows))


def _list(owner_id: UUID, rows: list[SimpleNamespace], cursor: str | None = None) -> str | None:
    page = list_runs(
        _session(rows), owner_id, NO_FILTERS, limit=1, cursor=cursor, secret=SECRET, now=NOW
    )
    return page.next_cursor


def _cursor_of(owner_id: UUID) -> str:
    cursor = _list(owner_id, [_run_row(timedelta(hours=1)), _run_row(timedelta(hours=2))])
    assert cursor is not None
    return cursor


def test_cursor_de_uma_conta_e_recusado_na_listagem_de_outra() -> None:
    cursor = _cursor_of(uuid7())

    # A conta entra na assinatura, não no escopo: o erro é de assinatura, não de filtros.
    with pytest.raises(InvalidCursorError) as caught:
        _list(uuid7(), [], cursor)
    assert not isinstance(caught.value, CursorScopeMismatchError)


def test_cursor_nao_carrega_o_id_da_conta_em_claro() -> None:
    owner_id = uuid7()
    raw = unb64url(_cursor_of(owner_id).split(".")[0])

    assert str(owner_id).encode() not in raw
    assert owner_id.hex.encode() not in raw


def test_cursor_continua_valendo_para_a_propria_conta() -> None:
    owner_id = uuid7()
    cursor = _cursor_of(owner_id)

    assert _list(owner_id, [], cursor) is None


def _v1_cursor(owner_id: UUID, secret: bytes) -> str:
    """Cursor no formato de antes do deploy: dono no escopo em claro e HMAC só do body."""
    payload = {
        "v": 1,
        "d": "desc",
        "f": {**NO_FILTERS.cursor_scope(), "owner_id": str(owner_id)},
        "t": (NOW - timedelta(hours=1)).isoformat(),
        "i": str(uuid7()),
        "x": int((NOW + timedelta(hours=1)).timestamp()),
    }
    body = b64url(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    return f"{body}.{b64url(hmac.new(secret, body.encode(), hashlib.sha256).digest())}"


def test_http_responde_400_ao_cursor_de_outra_conta() -> None:
    settings = Settings(environment="test", db_port=1, db_connect_timeout_s=1)
    app = create_app(settings)
    rows = [_run_row(timedelta(hours=1)), _run_row(timedelta(hours=2))]
    app.dependency_overrides[get_session] = lambda: _session(rows)
    sessions = SimulatedSessions(app, NOW)
    other = uuid7()

    with TestClient(app) as client:
        sessions.sign_in(uuid7())
        cursor = client.get(RUNS_URL, params={"limit": 1}).json()["next_cursor"]
        sessions.sign_in(other)
        response = client.get(RUNS_URL, params={"limit": 1, "cursor": cursor})
        v1 = _v1_cursor(other, settings.cursor_signing_key())
        v1_response = client.get(RUNS_URL, params={"limit": 1, "cursor": v1})

    body = assert_problem(response, 400, "BAD_REQUEST")
    v1_body = assert_problem(v1_response, 400, "BAD_REQUEST")
    # Mesma resposta para outra conta e para o formato antigo: o detalhe não vira oráculo.
    assert body["detail"] == v1_body["detail"]
