"""Aceite versionado dos termos, contra o PostgreSQL real (1.8; ADR 029).

A conta guarda a versão aceita e a data do aceite. Aceitar de novo a mesma versão não muda a
data; uma versão nova exige novo aceite. Contas do operador (`gs-admin`) não passam pelo aceite.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity import (
    CURRENT_TERMS_VERSION,
    ExternalIdentity,
    UnknownAccountError,
    accept_terms,
    find_account,
    resolve_login,
)
from goblinshift.identity.tables import app_user
from goblinshift.platform.db import unit_of_work
from tests.identity.support import new_user

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=3)
NEXT_VERSION = "versao-seguinte-de-teste"


def external_account(factory: sessionmaker[Session]) -> UUID:
    identity = ExternalIdentity(
        issuer="https://accounts.google.com", subject=uuid4().hex, display_name="Conta Google"
    )
    with unit_of_work(factory) as session:
        result = resolve_login(session, identity, signup_open=True, now=NOW)
    assert isinstance(result, UUID)
    return result


def accept(
    factory: sessionmaker[Session], owner_id: UUID, version: str, now: datetime
) -> tuple[str, datetime, bool]:
    with unit_of_work(factory) as session:
        accepted = accept_terms(session, owner_id, version=version, now=now)
    return accepted.version, accepted.accepted_at, accepted.newly_accepted


def stored_terms(factory: sessionmaker[Session], owner_id: UUID) -> tuple[object, object]:
    with unit_of_work(factory) as session:
        row = session.execute(
            select(app_user.c.terms_version, app_user.c.terms_accepted_at).where(
                app_user.c.id == owner_id
            )
        ).one()
    return row.terms_version, row.terms_accepted_at


def needs_acceptance(factory: sessionmaker[Session], owner_id: UUID, version: str) -> bool:
    with unit_of_work(factory) as session:
        account = find_account(session, owner_id)
    assert account is not None
    return account.needs_terms_acceptance(version)


def test_versao_vigente_e_um_rascunho_dentro_do_limite_do_banco() -> None:
    assert "rascunho" in CURRENT_TERMS_VERSION
    assert 1 <= len(CURRENT_TERMS_VERSION) <= 64


def test_conta_nova_do_login_precisa_aceitar(factory: sessionmaker[Session]) -> None:
    owner = external_account(factory)

    assert needs_acceptance(factory, owner, CURRENT_TERMS_VERSION)
    assert stored_terms(factory, owner) == (None, None)


def test_aceite_grava_versao_e_data(factory: sessionmaker[Session]) -> None:
    owner = external_account(factory)

    result = accept(factory, owner, CURRENT_TERMS_VERSION, NOW)

    assert result == (CURRENT_TERMS_VERSION, NOW, True)
    assert stored_terms(factory, owner) == (CURRENT_TERMS_VERSION, NOW)
    assert not needs_acceptance(factory, owner, CURRENT_TERMS_VERSION)


def test_aceitar_de_novo_a_mesma_versao_mantem_a_data_do_primeiro(
    factory: sessionmaker[Session],
) -> None:
    owner = external_account(factory)
    accept(factory, owner, CURRENT_TERMS_VERSION, NOW)

    result = accept(factory, owner, CURRENT_TERMS_VERSION, LATER)

    assert result == (CURRENT_TERMS_VERSION, NOW, False)
    assert stored_terms(factory, owner) == (CURRENT_TERMS_VERSION, NOW)


def test_versao_nova_exige_novo_aceite(factory: sessionmaker[Session]) -> None:
    owner = external_account(factory)
    accept(factory, owner, CURRENT_TERMS_VERSION, NOW)

    assert needs_acceptance(factory, owner, NEXT_VERSION)

    result = accept(factory, owner, NEXT_VERSION, LATER)

    assert result == (NEXT_VERSION, LATER, True)
    assert not needs_acceptance(factory, owner, NEXT_VERSION)


def test_aceite_de_uma_conta_nao_muda_a_outra(factory: sessionmaker[Session]) -> None:
    owner = external_account(factory)
    other = external_account(factory)

    accept(factory, owner, CURRENT_TERMS_VERSION, NOW)

    assert stored_terms(factory, other) == (None, None)
    assert needs_acceptance(factory, other, CURRENT_TERMS_VERSION)


def test_conta_do_operador_nao_passa_pelo_aceite(factory: sessionmaker[Session]) -> None:
    operator = new_user(factory)

    assert not needs_acceptance(factory, operator, CURRENT_TERMS_VERSION)


def test_aceite_de_conta_inexistente_e_erro(factory: sessionmaker[Session]) -> None:
    with pytest.raises(UnknownAccountError):
        accept(factory, uuid4(), CURRENT_TERMS_VERSION, NOW)


def test_dois_aceites_simultaneos_gravam_um_so_aceite(factory: sessionmaker[Session]) -> None:
    owner = external_account(factory)
    barrier = Barrier(2)

    def accept_at(now: datetime) -> bool:
        with unit_of_work(factory) as session:
            barrier.wait()
            return accept_terms(
                session, owner, version=CURRENT_TERMS_VERSION, now=now
            ).newly_accepted

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(accept_at, [NOW, LATER]))

    assert sorted(results) == [False, True]
    _, accepted_at = stored_terms(factory, owner)
    assert accepted_at in {NOW, LATER}
