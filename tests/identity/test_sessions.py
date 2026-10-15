"""Sessão web no PostgreSQL: emissão, autenticação, expiração e revogação (3.6)."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity import (
    IssuedSession,
    Principal,
    Scope,
    authenticate_api_key,
    authenticate_session,
    csrf_token_matches,
    revoke_all_credentials,
    revoke_session,
    start_session,
)
from goblinshift.identity.tables import api_key, app_user, web_session
from goblinshift.platform.db import unit_of_work
from tests.identity.support import new_key, new_user

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
CSRF_KEY = b"k" * 32


def _start(
    factory: sessionmaker[Session],
    user_id: UUID,
    *,
    authenticated_at: datetime = NOW,
    now: datetime = NOW,
) -> IssuedSession:
    with unit_of_work(factory) as session:
        return start_session(
            session, user_id=user_id, authenticated_at=authenticated_at, now=now, csrf_key=CSRF_KEY
        )


def _authenticate(factory: sessionmaker[Session], token: str, now: datetime) -> Principal | None:
    with unit_of_work(factory) as session:
        return authenticate_session(session, token, now)


def _row(factory: sessionmaker[Session], session_id: UUID) -> dict[str, object]:
    with factory() as session:
        row = session.execute(select(web_session).where(web_session.c.id == session_id)).one()
    return dict(row._mapping)


# --- Emissão ----------------------------------------------------------------------------------


def test_sessao_emitida_autentica_com_todos_os_escopos(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    issued = _start(factory, user_id, authenticated_at=NOW - timedelta(seconds=5))

    principal = _authenticate(factory, issued.token, NOW)

    assert principal == Principal(
        owner_id=user_id,
        scopes=frozenset(Scope),
        via="session",
        session_id=issued.session_id,
        authenticated_at=NOW - timedelta(seconds=5),
    )


def test_banco_guarda_so_o_sha256_do_token(factory: sessionmaker[Session]) -> None:
    issued = _start(factory, new_user(factory))

    row = _row(factory, issued.session_id)

    assert row["secret_hash"] == hashlib.sha256(issued.token.encode("ascii")).digest()
    assert issued.token not in repr(row)
    assert issued.csrf_token not in repr(row)


def test_prazos_da_sessao_nova(factory: sessionmaker[Session]) -> None:
    issued = _start(factory, new_user(factory), authenticated_at=NOW - timedelta(minutes=1))

    assert issued.idle_expires_at == NOW + timedelta(minutes=30)
    assert issued.absolute_expires_at == NOW + timedelta(hours=8) - timedelta(minutes=1)
    row = _row(factory, issued.session_id)
    assert row["last_seen_at"] == NOW
    assert row["idle_expires_at"] == issued.idle_expires_at
    assert row["absolute_expires_at"] == issued.absolute_expires_at


def test_inatividade_nunca_passa_do_teto_absoluto(factory: sessionmaker[Session]) -> None:
    issued = _start(
        factory, new_user(factory), authenticated_at=NOW - timedelta(hours=7, minutes=50)
    )

    assert issued.idle_expires_at == issued.absolute_expires_at == NOW + timedelta(minutes=10)


@pytest.mark.parametrize(
    "age",
    [pytest.param(timedelta(hours=8), id="8h"), pytest.param(timedelta(hours=9), id="9h")],
)
def test_login_no_provedor_de_8h_ou_mais_nao_abre_sessao(
    factory: sessionmaker[Session], age: timedelta
) -> None:
    with pytest.raises(ValueError, match="8 h"):
        _start(factory, new_user(factory), authenticated_at=NOW - age)


def test_token_e_csrf_mudam_a_cada_sessao(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)

    first, second = _start(factory, user_id), _start(factory, user_id)

    assert first.session_id != second.session_id
    assert first.token != second.token
    assert first.csrf_token != second.csrf_token


def test_csrf_emitido_e_ligado_a_sessao(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    first, second = _start(factory, user_id), _start(factory, user_id)

    assert csrf_token_matches(CSRF_KEY, first.session_id, first.csrf_token, first.csrf_token)
    assert not csrf_token_matches(CSRF_KEY, second.session_id, first.csrf_token, first.csrf_token)


def test_repr_da_sessao_emitida_nao_mostra_tokens(factory: sessionmaker[Session]) -> None:
    issued = _start(factory, new_user(factory))

    assert issued.token not in repr(issued)
    assert issued.csrf_token not in repr(issued)


# --- Tokens recusados -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    [
        pytest.param("", id="vazio"),
        pytest.param("A" * 43, id="desconhecido"),
        pytest.param("A" * 42, id="curto"),
        pytest.param("A" * 44, id="longo"),
        pytest.param("é" * 43, id="nao-ascii"),
        pytest.param("A" * 42 + "\n", id="quebra-de-linha"),
        pytest.param("A" * 42 + "\x00", id="nul"),
        pytest.param("A" * 10_000, id="enorme"),
    ],
)
def test_token_desconhecido_ou_malformado_nao_autentica(
    factory: sessionmaker[Session], token: str
) -> None:
    _start(factory, new_user(factory))

    assert _authenticate(factory, token, NOW) is None


# --- Expiração --------------------------------------------------------------------------------


def test_sessao_aceita_com_29_59_sem_uso(factory: sessionmaker[Session]) -> None:
    issued = _start(factory, new_user(factory))

    assert _authenticate(factory, issued.token, NOW + timedelta(minutes=29, seconds=59))


def test_sessao_expira_com_30_min_sem_uso(factory: sessionmaker[Session]) -> None:
    issued = _start(factory, new_user(factory))

    assert _authenticate(factory, issued.token, NOW + timedelta(minutes=30)) is None


def test_uso_estende_a_inatividade(factory: sessionmaker[Session]) -> None:
    issued = _start(factory, new_user(factory))
    used_at = NOW + timedelta(minutes=20)
    assert _authenticate(factory, issued.token, used_at)

    assert _authenticate(factory, issued.token, used_at + timedelta(minutes=29, seconds=59))
    assert _authenticate(factory, issued.token, used_at + timedelta(minutes=60)) is None


def test_sessao_expira_8h_apos_login_mesmo_com_uso(factory: sessionmaker[Session]) -> None:
    issued = _start(factory, new_user(factory))
    for step in range(1, 24):
        assert _authenticate(factory, issued.token, NOW + step * timedelta(minutes=20))
    assert _authenticate(factory, issued.token, NOW + timedelta(hours=8) - timedelta(seconds=1))

    assert _authenticate(factory, issued.token, NOW + timedelta(hours=8)) is None


def test_teto_absoluto_conta_do_login_no_provedor(factory: sessionmaker[Session]) -> None:
    issued = _start(factory, new_user(factory), authenticated_at=NOW - timedelta(hours=7))
    assert _authenticate(factory, issued.token, NOW + timedelta(minutes=29))
    assert _authenticate(factory, issued.token, NOW + timedelta(minutes=58))

    assert _authenticate(factory, issued.token, NOW + timedelta(minutes=59, seconds=59))
    assert _authenticate(factory, issued.token, NOW + timedelta(hours=1)) is None


# --- last_seen_at -----------------------------------------------------------------------------


def test_last_seen_nao_grava_antes_de_um_minuto(factory: sessionmaker[Session]) -> None:
    issued = _start(factory, new_user(factory))

    assert _authenticate(factory, issued.token, NOW + timedelta(seconds=59))

    row = _row(factory, issued.session_id)
    assert row["last_seen_at"] == NOW
    assert row["idle_expires_at"] == NOW + timedelta(minutes=30)


def test_last_seen_grava_com_um_minuto(factory: sessionmaker[Session]) -> None:
    issued = _start(factory, new_user(factory))
    used_at = NOW + timedelta(minutes=1)

    assert _authenticate(factory, issued.token, used_at)

    row = _row(factory, issued.session_id)
    assert row["last_seen_at"] == used_at
    assert row["idle_expires_at"] == used_at + timedelta(minutes=30)


def test_last_seen_respeita_o_teto_absoluto(factory: sessionmaker[Session]) -> None:
    issued = _start(
        factory, new_user(factory), authenticated_at=NOW - timedelta(hours=7, minutes=50)
    )
    used_at = NOW + timedelta(minutes=5)

    assert _authenticate(factory, issued.token, used_at)

    row = _row(factory, issued.session_id)
    assert row["last_seen_at"] == used_at
    assert row["idle_expires_at"] == issued.absolute_expires_at


# --- Conta ------------------------------------------------------------------------------------


def test_conta_bloqueada_invalida_a_sessao(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    issued = _start(factory, user_id)
    with unit_of_work(factory) as session:
        session.execute(update(app_user).where(app_user.c.id == user_id).values(status="blocked"))

    assert _authenticate(factory, issued.token, NOW) is None


def test_exclusao_pedida_invalida_a_sessao(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    issued = _start(factory, user_id)
    with unit_of_work(factory) as session:
        session.execute(
            update(app_user).where(app_user.c.id == user_id).values(deletion_requested_at=NOW)
        )

    assert _authenticate(factory, issued.token, NOW) is None


# --- Revogação --------------------------------------------------------------------------------


def test_sessao_revogada_nao_autentica(factory: sessionmaker[Session]) -> None:
    issued = _start(factory, new_user(factory))
    with unit_of_work(factory) as session:
        revoke_session(session, issued.session_id, NOW)

    assert _authenticate(factory, issued.token, NOW) is None


def test_revogar_de_novo_mantem_o_primeiro_instante(factory: sessionmaker[Session]) -> None:
    issued = _start(factory, new_user(factory))
    with unit_of_work(factory) as session:
        revoke_session(session, issued.session_id, NOW)
    with unit_of_work(factory) as session:
        revoke_session(session, issued.session_id, NOW + timedelta(minutes=5))

    assert _row(factory, issued.session_id)["revoked_at"] == NOW


def test_revogar_uma_sessao_nao_afeta_as_outras(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    first, second = _start(factory, user_id), _start(factory, user_id)
    with unit_of_work(factory) as session:
        revoke_session(session, first.session_id, NOW)

    assert _authenticate(factory, second.token, NOW)


def test_revoke_all_revoga_sessoes_e_chaves_so_da_conta(factory: sessionmaker[Session]) -> None:
    owner, other = new_user(factory), new_user(factory)
    owner_sessions = [_start(factory, owner), _start(factory, owner)]
    owner_keys = [new_key(factory, owner, now=NOW), new_key(factory, owner, now=NOW)]
    other_session = _start(factory, other)
    other_key = new_key(factory, other, now=NOW)

    with unit_of_work(factory) as session:
        revoke_all_credentials(session, owner, NOW)

    for issued in owner_sessions:
        assert _authenticate(factory, issued.token, NOW) is None
    with unit_of_work(factory) as session:
        for key in owner_keys:
            assert authenticate_api_key(session, key.token, NOW) is None
        assert authenticate_api_key(session, other_key.token, NOW) is not None
    assert _authenticate(factory, other_session.token, NOW) is not None


def test_revoke_all_de_novo_mantem_o_primeiro_instante(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    issued = _start(factory, owner)
    new_key(factory, owner, now=NOW)
    with unit_of_work(factory) as session:
        revoke_all_credentials(session, owner, NOW)
    with unit_of_work(factory) as session:
        revoke_all_credentials(session, owner, NOW + timedelta(minutes=5))

    assert _row(factory, issued.session_id)["revoked_at"] == NOW
    with factory() as session:
        revoked = session.execute(
            select(api_key.c.revoked_at).where(api_key.c.owner_id == owner)
        ).scalar_one()
    assert revoked == NOW
