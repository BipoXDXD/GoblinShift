"""Bloqueio, liberação e pedido de exclusão da conta (docs/GoblinShiftPlan.md, 1.8; Frente C)."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity import (
    ExternalIdentity,
    LoginRefused,
    UnknownAccountError,
    authenticate_api_key,
    block_account,
    request_account_deletion,
    resolve_login,
    unblock_account,
)
from goblinshift.identity.accounts import list_accounts, list_pending_accounts
from goblinshift.identity.tables import api_key, app_user, web_session
from goblinshift.platform.db import unit_of_work
from tests.identity.support import new_key, new_user

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def new_web_session(factory: sessionmaker[Session], user_id: UUID) -> UUID:
    """Linha de sessão como a Frente B a grava, sem passar pelo login."""
    with unit_of_work(factory) as session:
        session_id: UUID = session.execute(
            insert(web_session)
            .values(
                user_id=user_id,
                secret_hash=hashlib.sha256(secrets.token_bytes(32)).digest(),
                authenticated_at=NOW,
                last_seen_at=NOW,
                idle_expires_at=NOW + timedelta(minutes=30),
                absolute_expires_at=NOW + timedelta(hours=8),
            )
            .returning(web_session.c.id)
        ).scalar_one()
    return session_id


def account_row(factory: sessionmaker[Session], user_id: UUID) -> dict[str, object]:
    with factory() as session:
        row = session.execute(select(app_user).where(app_user.c.id == user_id)).one()
    return dict(row._mapping)


def open_credentials(factory: sessionmaker[Session], user_id: UUID) -> tuple[int, int]:
    """Quantas chaves e sessões da conta ainda não foram revogadas."""
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


# --- Bloqueio ---------------------------------------------------------------------------------


def test_bloqueio_marca_a_conta_e_revoga_sessoes_e_chaves(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    issued = new_key(factory, user_id, now=NOW)
    new_web_session(factory, user_id)

    with unit_of_work(factory) as session:
        found = block_account(session, user_id, NOW)

    assert found is True
    assert account_row(factory, user_id)["status"] == "blocked"
    assert open_credentials(factory, user_id) == (0, 0)
    with unit_of_work(factory) as session:
        assert authenticate_api_key(session, issued.token, NOW) is None


def test_bloquear_de_novo_e_sucesso(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    with unit_of_work(factory) as session:
        block_account(session, user_id, NOW)

    with unit_of_work(factory) as session:
        found = block_account(session, user_id, NOW + timedelta(minutes=1))

    assert found is True
    assert account_row(factory, user_id)["status"] == "blocked"


def test_bloquear_conta_inexistente_devolve_false(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        assert block_account(session, uuid7(), NOW) is False


def test_bloqueio_nao_toca_outra_conta(factory: sessionmaker[Session]) -> None:
    blocked, other = new_user(factory), new_user(factory)
    issued = new_key(factory, other, now=NOW)
    new_web_session(factory, other)

    with unit_of_work(factory) as session:
        block_account(session, blocked, NOW)

    assert account_row(factory, other)["status"] == "active"
    assert open_credentials(factory, other) == (1, 1)
    with unit_of_work(factory) as session:
        assert authenticate_api_key(session, issued.token, NOW) is not None


def test_liberar_reativa_a_conta_sem_ressuscitar_chaves(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    issued = new_key(factory, user_id, now=NOW)
    with unit_of_work(factory) as session:
        block_account(session, user_id, NOW)

    with unit_of_work(factory) as session:
        found = unblock_account(session, user_id)

    assert found is True
    assert account_row(factory, user_id)["status"] == "active"
    with unit_of_work(factory) as session:
        assert authenticate_api_key(session, issued.token, NOW) is None


def test_liberar_conta_inexistente_devolve_false(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        assert unblock_account(session, uuid7()) is False


# --- Pedido de exclusão -----------------------------------------------------------------------


def test_exclusao_pedida_marca_o_instante_e_revoga_sessoes_e_chaves(
    factory: sessionmaker[Session],
) -> None:
    user_id = new_user(factory)
    issued = new_key(factory, user_id, now=NOW)
    new_web_session(factory, user_id)

    with unit_of_work(factory) as session:
        request_account_deletion(session, user_id, NOW)

    assert account_row(factory, user_id)["deletion_requested_at"] == NOW
    assert open_credentials(factory, user_id) == (0, 0)
    with unit_of_work(factory) as session:
        assert authenticate_api_key(session, issued.token, NOW) is None


def test_pedir_exclusao_de_novo_mantem_o_primeiro_pedido(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    with unit_of_work(factory) as session:
        request_account_deletion(session, user_id, NOW)

    with unit_of_work(factory) as session:
        request_account_deletion(session, user_id, NOW + timedelta(hours=1))

    assert account_row(factory, user_id)["deletion_requested_at"] == NOW


def test_pedir_exclusao_de_conta_inexistente_e_erro(factory: sessionmaker[Session]) -> None:
    with pytest.raises(UnknownAccountError), unit_of_work(factory) as session:
        request_account_deletion(session, uuid7(), NOW)


def test_chave_de_conta_em_exclusao_nao_autentica_mesmo_sem_revogacao(
    factory: sessionmaker[Session],
) -> None:
    # Defesa em profundidade: a marca basta, mesmo se a revogação não tivesse chegado à chave.
    user_id = new_user(factory)
    issued = new_key(factory, user_id, now=NOW)
    with unit_of_work(factory) as session:
        session.execute(
            update(app_user).where(app_user.c.id == user_id).values(deletion_requested_at=NOW)
        )

    with unit_of_work(factory) as session:
        assert authenticate_api_key(session, issued.token, NOW) is None


# --- Login -------------------------------------------------------------------------------------


def _identity_of(factory: sessionmaker[Session], user_id: UUID) -> ExternalIdentity:
    row = account_row(factory, user_id)
    return ExternalIdentity(
        issuer=str(row["issuer"]), subject=str(row["subject"]), display_name="Conta de teste"
    )


def test_conta_em_exclusao_nao_loga(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    with unit_of_work(factory) as session:
        request_account_deletion(session, user_id, NOW)

    with unit_of_work(factory) as session:
        outcome = resolve_login(session, _identity_of(factory, user_id), signup_open=True, now=NOW)

    assert outcome is LoginRefused.DELETION_REQUESTED


def test_conta_bloqueada_nao_completa_login(factory: sessionmaker[Session]) -> None:
    # Já teve sessão: bloqueio por abuso, e não a conta pendente do cadastro fechado (ADR 023).
    user_id = new_user(factory)
    new_web_session(factory, user_id)
    with unit_of_work(factory) as session:
        block_account(session, user_id, NOW)

    with unit_of_work(factory) as session:
        outcome = resolve_login(session, _identity_of(factory, user_id), signup_open=True, now=NOW)

    assert outcome is LoginRefused.BLOCKED


# --- Listagem para o administrador ------------------------------------------------------------


def test_listagem_traz_id_emissor_estado_e_criacao_sem_sujeito(
    factory: sessionmaker[Session],
) -> None:
    user_id = new_user(factory)

    with unit_of_work(factory) as session:
        [listed] = list_accounts(session)

    assert listed.id == user_id
    assert listed.status == "active"
    assert listed.issuer == account_row(factory, user_id)["issuer"]
    assert not hasattr(listed, "subject")


def test_pendentes_sao_as_bloqueadas_que_nunca_tiveram_sessao(
    factory: sessionmaker[Session],
) -> None:
    active = new_user(factory)
    pending = new_user(factory, blocked=True)
    blocked_after_use = new_user(factory)
    new_web_session(factory, blocked_after_use)
    with unit_of_work(factory) as session:
        block_account(session, blocked_after_use, NOW)

    with unit_of_work(factory) as session:
        listed = {account.id for account in list_pending_accounts(session)}

    assert listed == {pending}
    assert active not in listed
