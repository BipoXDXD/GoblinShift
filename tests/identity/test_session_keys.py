"""Chaves emitidas, listadas e revogadas pela sessão web (GoblinShiftPlan.md, 3.6; Frente C)."""

import io
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity import (
    MAX_ACTIVE_KEYS,
    ActiveKeyLimitError,
    InvalidKeyLifetimeError,
    InvalidScopesError,
    IssuedKey,
    Scope,
    UnknownAccountError,
    authenticate_api_key,
    issue_key,
    issue_key_for_session,
    list_keys,
    revoke_owned_key,
)
from goblinshift.identity.accounts import MAX_LISTED_KEYS, StoredKeyLimitError
from goblinshift.identity.tables import api_key, app_user
from goblinshift.platform.db import unit_of_work
from tests.identity.support import HASHER, new_user
from tests.lock_support import in_thread, wait_until_someone_waits_for_a_lock

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
READ = frozenset({Scope.FILES_READ})


def issue(factory: sessionmaker[Session], owner_id: UUID, now: datetime = NOW) -> IssuedKey:
    with unit_of_work(factory) as session:
        return issue_key_for_session(session, owner_id=owner_id, scopes=READ, days=30, now=now)


def revoke(factory: sessionmaker[Session], owner_id: UUID, key: IssuedKey) -> None:
    with unit_of_work(factory) as session:
        session.execute(
            update(api_key)
            .where(api_key.c.id == key.id)
            .values(revoked_at=NOW - timedelta(hours=1))
        )


def expire(factory: sessionmaker[Session], owner_id: UUID, key: IssuedKey) -> None:
    with unit_of_work(factory) as session:
        session.execute(
            update(api_key)
            .where(api_key.c.id == key.id)
            .values(created_at=NOW - timedelta(days=30), expires_at=NOW)
        )


def block_account(factory: sessionmaker[Session], owner_id: UUID, key: IssuedKey) -> None:
    with unit_of_work(factory) as session:
        session.execute(update(app_user).where(app_user.c.id == owner_id).values(status="blocked"))


def request_deletion(factory: sessionmaker[Session], owner_id: UUID, key: IssuedKey) -> None:
    with unit_of_work(factory) as session:
        session.execute(
            update(app_user).where(app_user.c.id == owner_id).values(deletion_requested_at=NOW)
        )


def active_count(factory: sessionmaker[Session], owner_id: UUID) -> int:
    with factory() as session:
        count: int = session.execute(
            select(func.count())
            .select_from(api_key)
            .where(api_key.c.owner_id == owner_id, api_key.c.revoked_at.is_(None))
        ).scalar_one()
    return count


def test_chave_emitida_pela_sessao_autentica_com_os_escopos_pedidos(
    factory: sessionmaker[Session],
) -> None:
    owner = new_user(factory)
    scopes = frozenset({Scope.RUNS_READ, Scope.RUNS_WRITE})

    with unit_of_work(factory) as session:
        issued = issue_key_for_session(session, owner_id=owner, scopes=scopes, days=7, now=NOW)

    assert issued.expires_at == NOW + timedelta(days=7)
    with unit_of_work(factory) as session:
        principal = authenticate_api_key(session, issued.token, NOW, hasher=HASHER)
    assert principal is not None
    assert principal.owner_id == owner
    assert principal.scopes == scopes


def test_sexta_chave_ativa_e_recusada(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    for _ in range(MAX_ACTIVE_KEYS):
        issue(factory, owner)

    with pytest.raises(ActiveKeyLimitError), unit_of_work(factory) as session:
        issue_key_for_session(session, owner_id=owner, scopes=READ, days=30, now=NOW)

    assert active_count(factory, owner) == MAX_ACTIVE_KEYS


def test_chave_revogada_ou_vencida_libera_vaga(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    for _ in range(MAX_ACTIVE_KEYS - 1):
        issue(factory, owner)
    # Uma vencida (emitida há 31 dias com validade de 30) e uma revogada não contam.
    with unit_of_work(factory) as session:
        issue_key_for_session(
            session, owner_id=owner, scopes=READ, days=30, now=NOW - timedelta(days=30)
        )
    revoked = issue(factory, owner)
    with unit_of_work(factory) as session:
        [summary] = [k for k in list_keys(session, owner, NOW) if k.public_id == revoked.public_id]
        revoke_owned_key(session, owner, summary.id, NOW)

    issue(factory, owner)


def test_vencida_no_instante_exato_nao_conta(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    for _ in range(MAX_ACTIVE_KEYS - 1):
        issue(factory, owner)
    with unit_of_work(factory) as session:
        issue_key_for_session(
            session, owner_id=owner, scopes=READ, days=1, now=NOW - timedelta(days=1)
        )

    issue(factory, owner)


def test_um_segundo_antes_de_vencer_ainda_conta(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    for _ in range(MAX_ACTIVE_KEYS - 1):
        issue(factory, owner)
    with unit_of_work(factory) as session:
        issue_key_for_session(
            session,
            owner_id=owner,
            scopes=READ,
            days=1,
            now=NOW - timedelta(days=1) + timedelta(seconds=1),
        )

    with pytest.raises(ActiveKeyLimitError):
        issue(factory, owner)


def store_inactive_keys(factory: sessionmaker[Session], owner_id: UUID, count: int) -> None:
    """Guarda `count` chaves que já não autenticam: metade revogadas, metade vencidas."""
    with unit_of_work(factory) as session:
        for index in range(count):
            if index % 2:
                issue_key(
                    session, owner_id=owner_id, scopes=READ, now=NOW - timedelta(days=31), days=30
                )
            else:
                issued = issue_key(session, owner_id=owner_id, scopes=READ, now=NOW)
                session.execute(
                    update(api_key)
                    .where(api_key.c.public_id == issued.public_id)
                    .values(revoked_at=NOW)
                )


def stored_count(factory: sessionmaker[Session], owner_id: UUID) -> int:
    with factory() as session:
        count: int = session.execute(
            select(func.count()).select_from(api_key).where(api_key.c.owner_id == owner_id)
        ).scalar_one()
    return count


def test_emissao_com_100_chaves_guardadas_e_recusada(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    store_inactive_keys(factory, owner, 99)
    # Com 99 guardadas a emissão passa e grava a centésima.
    issue(factory, owner)

    with pytest.raises(StoredKeyLimitError):
        issue(factory, owner)

    assert stored_count(factory, owner) == 100


def test_teto_de_chaves_guardadas_e_por_conta(factory: sessionmaker[Session]) -> None:
    full = new_user(factory)
    store_inactive_keys(factory, full, 100)
    other = new_user(factory)

    issue(factory, other)

    assert stored_count(factory, other) == 1


def test_emissao_concorrente_espera_a_trava_e_ve_a_quinta_chave(
    factory: sessionmaker[Session],
) -> None:
    owner = new_user(factory)
    for _ in range(MAX_ACTIVE_KEYS - 1):
        issue(factory, owner)

    with factory() as first, first.begin():
        issue_key_for_session(first, owner_id=owner, scopes=READ, days=30, now=NOW)
        second = in_thread(lambda: issue(factory, owner))
        wait_until_someone_waits_for_a_lock(factory, "a segunda emissão")

    with pytest.raises(ActiveKeyLimitError):
        second.result()
    assert active_count(factory, owner) == MAX_ACTIVE_KEYS


@pytest.mark.parametrize("status_change", ["blocked", "deletion_requested"])
def test_conta_bloqueada_ou_em_exclusao_nao_emite_chave(
    factory: sessionmaker[Session], status_change: str
) -> None:
    owner = new_user(factory)
    values = {"status": "blocked"} if status_change == "blocked" else {"deletion_requested_at": NOW}
    with unit_of_work(factory) as session:
        session.execute(update(app_user).where(app_user.c.id == owner).values(**values))

    with pytest.raises(UnknownAccountError):
        issue(factory, owner)

    assert active_count(factory, owner) == 0


def test_conta_inexistente_nao_emite_chave(factory: sessionmaker[Session]) -> None:
    with pytest.raises(UnknownAccountError):
        issue(factory, uuid7())


def test_sem_escopo_e_recusado(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)

    with pytest.raises(InvalidScopesError), unit_of_work(factory) as session:
        issue_key_for_session(session, owner_id=owner, scopes=frozenset(), days=30, now=NOW)


@pytest.mark.parametrize("days", [0, 91])
def test_validade_fora_da_faixa_e_recusada(factory: sessionmaker[Session], days: int) -> None:
    owner = new_user(factory)

    with pytest.raises(InvalidKeyLifetimeError), unit_of_work(factory) as session:
        issue_key_for_session(session, owner_id=owner, scopes=READ, days=days, now=NOW)

    assert active_count(factory, owner) == 0


# --- Listagem ---------------------------------------------------------------------------------


def test_listagem_traz_so_as_chaves_da_conta_mais_recentes_primeiro(
    factory: sessionmaker[Session],
) -> None:
    owner, other = new_user(factory), new_user(factory)
    first = issue(factory, owner, NOW)
    second = issue(factory, owner, NOW + timedelta(minutes=1))
    issue(factory, other)

    with unit_of_work(factory) as session:
        listed = list_keys(session, owner, NOW)

    assert [key.public_id for key in listed] == [second.public_id, first.public_id]
    assert listed[0].scopes == READ
    assert listed[0].created_at == NOW + timedelta(minutes=1)
    assert listed[0].expires_at == NOW + timedelta(minutes=1, days=30)
    assert listed[0].last_used_at is None
    assert listed[0].revoked_at is None


def test_listar_chaves_mostra_ativa_antiga_depois_de_100_revogadas(
    factory: sessionmaker[Session],
) -> None:
    # O `gs-admin issue-key` não passa pelo teto de `MAX_STORED_KEYS`: sem priorizar as ativas,
    # 100 chaves mais novas tirariam da listagem uma chave que ainda autentica, e o dono não teria
    # o id para revogá-la.
    owner = new_user(factory)
    active = issue(factory, owner, NOW - timedelta(days=1))
    with unit_of_work(factory) as session:
        for minute in range(MAX_LISTED_KEYS):
            issue_key(
                session, owner_id=owner, scopes=READ, days=30, now=NOW + timedelta(minutes=minute)
            )
            session.execute(
                update(api_key)
                .where(api_key.c.owner_id == owner, api_key.c.revoked_at.is_(None))
                .where(api_key.c.public_id != active.public_id)
                .values(revoked_at=NOW + timedelta(minutes=minute))
            )

    with unit_of_work(factory) as session:
        listed = list_keys(session, owner, NOW + timedelta(hours=2))

    assert len(listed) == MAX_LISTED_KEYS
    assert listed[0].public_id == active.public_id


def test_listagem_poe_as_ativas_antes_das_vencidas_e_revogadas(
    factory: sessionmaker[Session],
) -> None:
    owner = new_user(factory)
    active = issue(factory, owner, NOW - timedelta(days=10))
    with unit_of_work(factory) as session:
        expired = issue_key_for_session(
            session, owner_id=owner, scopes=READ, days=1, now=NOW - timedelta(days=5)
        )
    revoked = issue(factory, owner, NOW - timedelta(days=1))
    with unit_of_work(factory) as session:
        session.execute(update(api_key).where(api_key.c.id == revoked.id).values(revoked_at=NOW))

    with unit_of_work(factory) as session:
        listed = list_keys(session, owner, NOW)

    assert [key.public_id for key in listed] == [
        active.public_id,
        revoked.public_id,
        expired.public_id,
    ]


def test_listagem_de_conta_sem_chaves_e_vazia(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        assert list_keys(session, new_user(factory), NOW) == []


# --- Revogação --------------------------------------------------------------------------------


def test_dono_revoga_a_propria_chave(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    issued = issue(factory, owner)
    with factory() as session:
        [summary] = list_keys(session, owner, NOW)

    with unit_of_work(factory) as session:
        found = revoke_owned_key(session, owner, summary.id, NOW)

    assert found is True
    with unit_of_work(factory) as session:
        assert authenticate_api_key(session, issued.token, NOW, hasher=HASHER) is None
        [after] = list_keys(session, owner, NOW)
    assert after.revoked_at == NOW


def test_revogar_de_novo_preserva_o_primeiro_instante(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    issue(factory, owner)
    with factory() as session:
        [summary] = list_keys(session, owner, NOW)
    with unit_of_work(factory) as session:
        revoke_owned_key(session, owner, summary.id, NOW)

    with unit_of_work(factory) as session:
        found = revoke_owned_key(session, owner, summary.id, NOW + timedelta(hours=1))
        [after] = list_keys(session, owner, NOW)

    assert found is True
    assert after.revoked_at == NOW


def test_revogar_chave_alheia_devolve_false_e_nao_a_toca(factory: sessionmaker[Session]) -> None:
    owner, intruder = new_user(factory), new_user(factory)
    issued = issue(factory, owner)
    with factory() as session:
        [summary] = list_keys(session, owner, NOW)

    with unit_of_work(factory) as session:
        found = revoke_owned_key(session, intruder, summary.id, NOW)

    assert found is False
    with unit_of_work(factory) as session:
        assert authenticate_api_key(session, issued.token, NOW, hasher=HASHER) is not None


def test_revogar_chave_inexistente_devolve_false(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        assert revoke_owned_key(session, new_user(factory), uuid7(), NOW) is False


@pytest.mark.parametrize(
    ("make_refused", "reason"),
    [
        (revoke, "expired_or_revoked"),
        (expire, "expired_or_revoked"),
        (block_account, "account_unavailable"),
        (request_deletion, "account_unavailable"),
    ],
)
def test_chave_recusada_registra_user_hash_e_nao_o_public_id(
    factory: sessionmaker[Session],
    listen: Callable[[logging.Logger], io.StringIO],
    caplog: pytest.LogCaptureFixture,
    make_refused: Callable[[sessionmaker[Session], UUID, IssuedKey], None],
    reason: str,
) -> None:
    owner = new_user(factory)
    issued = issue(factory, owner)
    make_refused(factory, owner, issued)
    # Em produção o root fica em INFO (`configure_logging`); aqui o nível vem do caplog.
    caplog.set_level(logging.INFO, logger="goblinshift.identity.accounts")
    stream = listen(logging.getLogger("goblinshift.identity.accounts"))

    with unit_of_work(factory) as session:
        assert authenticate_api_key(session, issued.token, NOW, hasher=HASHER) is None

    [line] = [json.loads(raw) for raw in stream.getvalue().splitlines()]
    assert line["message"] == "chave de API recusada"
    assert line["user.hash"] == HASHER.hash(owner)
    assert line["reason"] == reason
    assert "dropped_fields" not in line
    assert issued.public_id not in stream.getvalue()
