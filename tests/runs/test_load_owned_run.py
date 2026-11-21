"""Leitura da Run pelo dono: quem atende a requisição de uma conta só enxerga as Runs dela.

`load_owned_run` exige o dono; `load_run_any_owner` é da manutenção e do worker, que agem sobre
qualquer conta, e nenhuma rota pode usá-la (fitness test em `tests/security`).
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.runs.store import load_owned_run, load_run_any_owner
from tests.runs.support import insert_finished_run, new_owner

pytestmark = pytest.mark.integration


def stored_run(factory: sessionmaker[Session]) -> tuple[uuid.UUID, uuid.UUID]:
    owner_id = new_owner(factory)
    return owner_id, insert_finished_run(factory, owner_id, datetime.now(UTC))


def test_dono_le_a_propria_run(factory: sessionmaker[Session]) -> None:
    owner_id, run_id = stored_run(factory)

    with factory() as session:
        loaded = load_owned_run(session, owner_id, run_id)

    assert loaded is not None
    assert (loaded.run_id, loaded.owner_id) == (run_id, owner_id)


def test_outra_conta_nao_le_a_run(factory: sessionmaker[Session]) -> None:
    _, run_id = stored_run(factory)
    stranger = new_owner(factory)

    with factory() as session:
        assert load_owned_run(session, stranger, run_id) is None


def test_leitura_interna_ignora_o_dono(factory: sessionmaker[Session]) -> None:
    owner_id, run_id = stored_run(factory)

    with factory() as session:
        loaded = load_run_any_owner(session, run_id)

    assert loaded is not None
    assert (loaded.run_id, loaded.owner_id) == (run_id, owner_id)
