"""Contas e chaves de API contra o PostgreSQL real (docs/GoblinShiftPlan.md, 3.6)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity import (
    ADMIN_ISSUER,
    InvalidDisplayNameError,
    InvalidKeyLifetimeError,
    InvalidScopesError,
    Principal,
    Scope,
    UnknownAccountError,
    authenticate_api_key,
    create_admin_user,
    find_account,
    issue_key,
    revoke_key,
)
from goblinshift.identity.tables import api_key, app_user
from goblinshift.platform.db import unit_of_work
from tests.identity.support import new_key, new_user

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def authenticate(factory: sessionmaker[Session], token: str, now: datetime) -> Principal | None:
    with unit_of_work(factory) as session:
        return authenticate_api_key(session, token, now)


def last_used_at(factory: sessionmaker[Session], public_id: str) -> datetime | None:
    with unit_of_work(factory) as session:
        value: datetime | None = session.execute(
            select(api_key.c.last_used_at).where(api_key.c.public_id == public_id)
        ).scalar_one()
    return value


def test_conta_do_administrador_usa_emissor_fixo_e_nome_informado(
    factory: sessionmaker[Session],
) -> None:
    with unit_of_work(factory) as session:
        user_id = create_admin_user(session, "  Ana  ")
        row = session.execute(select(app_user).where(app_user.c.id == user_id)).one()

    assert row.issuer == ADMIN_ISSUER
    assert row.display_name == "Ana"
    assert row.status == "active"


@pytest.mark.parametrize("name", ["", "   ", "x" * 201])
def test_nome_vazio_ou_longo_demais_e_recusado(factory: sessionmaker[Session], name: str) -> None:
    with pytest.raises(InvalidDisplayNameError), unit_of_work(factory) as session:
        create_admin_user(session, name)


def test_nome_com_200_caracteres_e_aceito(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        user_id = create_admin_user(session, "x" * 200)
        account = find_account(session, user_id)

    assert account is not None
    assert len(account.display_name) == 200


def test_chave_emitida_autentica_com_dono_e_escopos(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    issued = new_key(factory, user_id, scopes=frozenset({Scope.FILES_READ}), now=NOW)

    principal = authenticate(factory, issued.token, NOW)

    assert principal == Principal(
        owner_id=user_id, scopes=frozenset({Scope.FILES_READ}), via="api_key"
    )
    assert issued.expires_at == NOW + timedelta(days=30)


def test_banco_guarda_so_o_hash_do_segredo(factory: sessionmaker[Session]) -> None:
    issued = new_key(factory, new_user(factory))

    with unit_of_work(factory) as session:
        row = session.execute(select(api_key)).one()

    assert row.public_id == issued.public_id
    assert len(row.secret_hash) == 32
    stored = repr(tuple(row))
    assert issued.token[-43:] not in stored


def test_chave_para_conta_inexistente_e_recusada(factory: sessionmaker[Session]) -> None:
    with pytest.raises(UnknownAccountError), unit_of_work(factory) as session:
        issue_key(session, owner_id=uuid7(), scopes=frozenset(Scope), now=NOW)


def test_chave_sem_escopos_e_recusada(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)

    with pytest.raises(InvalidScopesError), unit_of_work(factory) as session:
        issue_key(session, owner_id=user_id, scopes=frozenset(), now=NOW)


def test_validade_acima_de_90_dias_e_recusada(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)

    with pytest.raises(InvalidKeyLifetimeError), unit_of_work(factory) as session:
        issue_key(session, owner_id=user_id, scopes=frozenset(Scope), now=NOW, days=91)


def test_chave_que_expira_exatamente_agora_nao_autentica(
    factory: sessionmaker[Session],
) -> None:
    issued = new_key(factory, new_user(factory), now=NOW, days=1)
    expires_at = NOW + timedelta(days=1)

    assert authenticate(factory, issued.token, expires_at - timedelta(microseconds=1))
    assert authenticate(factory, issued.token, expires_at) is None


def test_chave_revogada_nao_autentica(factory: sessionmaker[Session]) -> None:
    issued = new_key(factory, new_user(factory), now=NOW)

    with unit_of_work(factory) as session:
        assert revoke_key(session, issued.public_id, NOW)

    assert authenticate(factory, issued.token, NOW) is None


def test_revogar_de_novo_e_sucesso_e_mantem_o_primeiro_instante(
    factory: sessionmaker[Session],
) -> None:
    issued = new_key(factory, new_user(factory), now=NOW)
    later = NOW + timedelta(hours=1)

    with unit_of_work(factory) as session:
        assert revoke_key(session, issued.public_id, NOW)
        assert revoke_key(session, issued.public_id, later)
        revoked_at = session.execute(select(api_key.c.revoked_at)).scalar_one()

    assert revoked_at == NOW


def test_revogar_chave_inexistente_informa_que_nao_achou(
    factory: sessionmaker[Session],
) -> None:
    with unit_of_work(factory) as session:
        assert not revoke_key(session, "0" * 16, NOW)


def test_conta_bloqueada_nao_autentica(factory: sessionmaker[Session]) -> None:
    issued = new_key(factory, new_user(factory, blocked=True), now=NOW)

    assert authenticate(factory, issued.token, NOW) is None


def test_segredo_errado_com_identificador_valido_nao_autentica(
    factory: sessionmaker[Session],
) -> None:
    issued = new_key(factory, new_user(factory), now=NOW)
    last = "B" if issued.token[-1] != "B" else "C"

    assert authenticate(factory, issued.token[:-1] + last, NOW) is None


def test_identificador_desconhecido_nao_autentica(factory: sessionmaker[Session]) -> None:
    issued = new_key(factory, new_user(factory), now=NOW)
    unknown = "gsk_" + "f" * 16 + issued.token[20:]

    assert authenticate(factory, unknown, NOW) is None


def test_chave_malformada_nao_autentica(factory: sessionmaker[Session]) -> None:
    assert authenticate(factory, "Bearer qualquer-coisa", NOW) is None


def test_ultimo_uso_e_gravado_no_maximo_uma_vez_por_minuto(
    factory: sessionmaker[Session],
) -> None:
    issued = new_key(factory, new_user(factory), now=NOW)
    first_use = NOW + timedelta(seconds=1)

    authenticate(factory, issued.token, first_use)
    assert last_used_at(factory, issued.public_id) == first_use

    authenticate(factory, issued.token, first_use + timedelta(seconds=59))
    assert last_used_at(factory, issued.public_id) == first_use

    authenticate(factory, issued.token, first_use + timedelta(seconds=60))
    assert last_used_at(factory, issued.public_id) == first_use + timedelta(seconds=60)


def test_uso_recusado_nao_grava_ultimo_uso(factory: sessionmaker[Session]) -> None:
    issued = new_key(factory, new_user(factory, blocked=True), now=NOW)

    authenticate(factory, issued.token, NOW)

    assert last_used_at(factory, issued.public_id) is None


def test_escopo_gravado_desconhecido_nao_concede_nada(factory: sessionmaker[Session]) -> None:
    issued = new_key(factory, new_user(factory), scopes=frozenset({Scope.RUNS_READ}), now=NOW)
    with unit_of_work(factory) as session:
        session.execute(api_key.update().values(scopes=["runs:read", "admin:everything"]))

    principal = authenticate(factory, issued.token, NOW)

    assert principal is not None
    assert principal.scopes == {Scope.RUNS_READ}


def test_conta_inexistente_nao_e_encontrada(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        assert find_account(session, UUID(int=0)) is None
