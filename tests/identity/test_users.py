"""Conta a partir da identidade externa, contra o PostgreSQL real (ADRs 008 e 023)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity import ExternalIdentity, LoginRefused, resolve_login
from goblinshift.identity.tables import app_user, web_session
from goblinshift.platform.db import unit_of_work

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
GOOGLE = ExternalIdentity(
    issuer="https://accounts.google.com", subject="1092837465", display_name="Conta Google"
)
GITHUB = ExternalIdentity(issuer="https://github.com", subject="583231", display_name="octocat")


def resolve(
    factory: sessionmaker[Session], identity: ExternalIdentity, *, signup_open: bool
) -> UUID | LoginRefused:
    with unit_of_work(factory) as session:
        return resolve_login(session, identity, signup_open=signup_open, now=NOW)


def active_account(factory: sessionmaker[Session], identity: ExternalIdentity) -> UUID:
    result = resolve(factory, identity, signup_open=True)
    assert isinstance(result, UUID)
    return result


def account_row(factory: sessionmaker[Session], identity: ExternalIdentity) -> dict[str, object]:
    with unit_of_work(factory) as session:
        row = (
            session.execute(
                select(app_user).where(
                    app_user.c.issuer == identity.issuer, app_user.c.subject == identity.subject
                )
            )
            .mappings()
            .one()
        )
    return dict(row)


def set_account(factory: sessionmaker[Session], user_id: UUID, **values: object) -> None:
    with unit_of_work(factory) as session:
        session.execute(update(app_user).where(app_user.c.id == user_id).values(**values))


def add_web_session(factory: sessionmaker[Session], user_id: UUID) -> None:
    with unit_of_work(factory) as session:
        session.execute(
            insert(web_session).values(
                user_id=user_id,
                secret_hash=b"\x01" * 32,
                authenticated_at=NOW,
                last_seen_at=NOW,
                idle_expires_at=NOW + timedelta(minutes=30),
                absolute_expires_at=NOW + timedelta(hours=8),
            )
        )


def count_accounts(factory: sessionmaker[Session]) -> int:
    with unit_of_work(factory) as session:
        return session.execute(select(func.count()).select_from(app_user)).scalar_one()


def test_cadastro_fechado_identidade_nova_nasce_bloqueada_e_pendente(
    factory: sessionmaker[Session],
) -> None:
    result = resolve(factory, GOOGLE, signup_open=False)

    assert result is LoginRefused.PENDING_APPROVAL
    row = account_row(factory, GOOGLE)
    assert row["status"] == "blocked"
    assert row["display_name"] == "Conta Google"
    assert row["created_at"] == NOW


def test_cadastro_aberto_identidade_nova_entra_ativa(factory: sessionmaker[Session]) -> None:
    result = resolve(factory, GITHUB, signup_open=True)

    assert isinstance(result, UUID)
    row = account_row(factory, GITHUB)
    assert row["id"] == result
    assert row["status"] == "active"


def test_conta_ativa_existente_entra_mesmo_com_cadastro_fechado(
    factory: sessionmaker[Session],
) -> None:
    user_id = active_account(factory, GITHUB)

    assert resolve(factory, GITHUB, signup_open=False) == user_id
    assert count_accounts(factory) == 1


def test_login_atualiza_o_nome_de_exibicao(factory: sessionmaker[Session]) -> None:
    user_id = active_account(factory, GITHUB)
    renamed = ExternalIdentity(GITHUB.issuer, GITHUB.subject, "octocat-novo")

    assert resolve(factory, renamed, signup_open=False) == user_id
    assert account_row(factory, GITHUB)["display_name"] == "octocat-novo"


def test_pendente_que_tenta_de_novo_continua_pendente(factory: sessionmaker[Session]) -> None:
    resolve(factory, GOOGLE, signup_open=False)

    assert resolve(factory, GOOGLE, signup_open=False) is LoginRefused.PENDING_APPROVAL
    assert count_accounts(factory) == 1


def test_conta_bloqueada_que_ja_teve_sessao_e_recusada_como_bloqueada(
    factory: sessionmaker[Session],
) -> None:
    user_id = active_account(factory, GOOGLE)
    add_web_session(factory, user_id)
    set_account(factory, user_id, status="blocked")

    assert resolve(factory, GOOGLE, signup_open=True) is LoginRefused.BLOCKED


def test_cadastro_aberto_nao_desbloqueia_conta_bloqueada(factory: sessionmaker[Session]) -> None:
    resolve(factory, GOOGLE, signup_open=False)

    assert resolve(factory, GOOGLE, signup_open=True) is LoginRefused.PENDING_APPROVAL
    assert account_row(factory, GOOGLE)["status"] == "blocked"


def test_exclusao_pedida_recusa_o_login(factory: sessionmaker[Session]) -> None:
    user_id = active_account(factory, GOOGLE)
    set_account(factory, user_id, deletion_requested_at=NOW - timedelta(hours=1))

    assert resolve(factory, GOOGLE, signup_open=True) is LoginRefused.DELETION_REQUESTED


def test_exclusao_pedida_vale_mais_que_o_bloqueio(factory: sessionmaker[Session]) -> None:
    user_id = active_account(factory, GOOGLE)
    set_account(factory, user_id, status="blocked", deletion_requested_at=NOW)

    assert resolve(factory, GOOGLE, signup_open=True) is LoginRefused.DELETION_REQUESTED


def test_mesmo_sujeito_em_provedores_diferentes_sao_contas_diferentes(
    factory: sessionmaker[Session],
) -> None:
    google = active_account(factory, GOOGLE)
    github = active_account(
        factory, ExternalIdentity(GITHUB.issuer, GOOGLE.subject, GITHUB.display_name)
    )

    assert google != github
    assert count_accounts(factory) == 2
