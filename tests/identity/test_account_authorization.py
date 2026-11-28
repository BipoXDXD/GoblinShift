"""Lógica de autorização das contas (`identity/accounts.py`): os casos que uma revisão apontou.

Cada teste reproduziria um defeito suspeito; os que passam sem correção documentam que a
regra já vale. As hipóteses:

- (a) `unblock_account` ativa conta com exclusão pedida;
- (b) `issue_key_for_session` concede escopo que a conta não tem, ou emite para conta inativa;
- (c) `authenticate_api_key` e `authenticate_session` divergem sobre status e exclusão;
- (d) bloqueio concorrente com um login em andamento deixa uma sessão viva;
- (e) `revoke_key` (por `public_id`, sem dono) alcançável fora da CLI do administrador;
- (f) emissão com o Principal lido antes do `logout-all` sobrevive com chave ativa (#442, 49b);
- (g) a trava da conta na emissão e no `logout-all` segura o login da mesma conta (#555).
"""

import hashlib
import secrets
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity import (
    ExternalIdentity,
    InvalidScopesError,
    LoginRefusal,
    LoginRefused,
    Scope,
    UnknownAccountError,
    authenticate_api_key,
    authenticate_session,
    block_account,
    issue_key_for_session,
    request_account_deletion,
    resolve_login,
    revoke_all_credentials,
    start_session,
    unblock_account,
)
from goblinshift.identity.tables import api_key, app_user, web_session
from goblinshift.platform.db import unit_of_work
from tests.identity.support import HASHER, new_key, new_user, new_web_session
from tests.lock_support import in_thread, wait_until_someone_waits_for_a_lock
from tests.sql_capture import captured_sql

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
CSRF_KEY = b"k" * 32
LOCK_WAIT_DEADLINE_S = 10.0
SRC = Path(__file__).resolve().parents[2] / "src" / "goblinshift"
IDENTITY = ExternalIdentity(
    issuer="https://accounts.google.com", subject="sub-concorrente", display_name="Conta Google"
)


def status_of(factory: sessionmaker[Session], user_id: UUID) -> str:
    with factory() as session:
        status: str = session.execute(
            select(app_user.c.status).where(app_user.c.id == user_id)
        ).scalar_one()
    return status


# --- (a) liberar não ressuscita conta em exclusão ---------------------------------------------


def test_unblock_nao_ativa_conta_com_exclusao_pedida(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    with unit_of_work(factory) as session:
        block_account(session, user_id, NOW)
        request_account_deletion(session, user_id, NOW)

    with unit_of_work(factory) as session:
        unblocked = unblock_account(session, user_id)

    assert unblocked is False
    assert status_of(factory, user_id) == "blocked"


def test_unblock_ativa_a_conta_pendente(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory, blocked=True)

    with unit_of_work(factory) as session:
        unblocked = unblock_account(session, user_id)

    assert unblocked is True
    assert status_of(factory, user_id) == "active"


# --- (b) emissão pela sessão ------------------------------------------------------------------


def test_emissao_sem_escopo_e_recusada(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)

    with pytest.raises(InvalidScopesError), unit_of_work(factory) as session:
        issue_key_for_session(
            session,
            owner_id=user_id,
            session_id=new_web_session(factory, user_id, now=NOW),
            scopes=frozenset(),
            days=30,
            now=NOW,
        )


@pytest.mark.parametrize("state", ["blocked", "deletion_requested"])
def test_emissao_para_conta_inativa_e_recusada(factory: sessionmaker[Session], state: str) -> None:
    user_id = new_user(factory, blocked=state == "blocked")
    if state == "deletion_requested":
        with unit_of_work(factory) as session:
            request_account_deletion(session, user_id, NOW)

    web = new_web_session(factory, user_id, now=NOW)

    with pytest.raises(UnknownAccountError), unit_of_work(factory) as session:
        issue_key_for_session(
            session,
            owner_id=user_id,
            session_id=web,
            scopes=frozenset({Scope.RUNS_READ}),
            days=30,
            now=NOW,
        )


def test_chave_emitida_tem_so_os_escopos_pedidos(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    web = new_web_session(factory, user_id, now=NOW)
    with unit_of_work(factory) as session:
        issued = issue_key_for_session(
            session,
            owner_id=user_id,
            session_id=web,
            scopes=frozenset({Scope.FILES_READ}),
            days=30,
            now=NOW,
        )

    with unit_of_work(factory) as session:
        principal = authenticate_api_key(session, issued.token, NOW, hasher=HASHER)

    assert principal is not None
    assert principal.scopes == frozenset({Scope.FILES_READ})


# --- (c) chave e sessão decidem igual ---------------------------------------------------------


def _set_state(factory: sessionmaker[Session], user_id: UUID, state: str) -> None:
    changes: dict[str, dict[str, object]] = {
        "active": {},
        "blocked": {"status": "blocked"},
        "deletion_requested": {"deletion_requested_at": NOW},
        "blocked_and_deletion_requested": {"status": "blocked", "deletion_requested_at": NOW},
    }
    values = changes[state]
    if values:
        with unit_of_work(factory) as session:
            session.execute(update(app_user).where(app_user.c.id == user_id).values(**values))


@pytest.mark.parametrize(
    ("state", "authenticates"),
    [
        ("active", True),
        ("blocked", False),
        ("deletion_requested", False),
        ("blocked_and_deletion_requested", False),
    ],
)
def test_chave_e_sessao_aceitam_e_recusam_a_mesma_conta(
    factory: sessionmaker[Session], state: str, authenticates: bool
) -> None:
    user_id = new_user(factory)
    key = new_key(factory, user_id, now=NOW)
    with unit_of_work(factory) as session:
        web = start_session(
            session, user_id=user_id, authenticated_at=NOW, now=NOW, csrf_key=CSRF_KEY
        )
    _set_state(factory, user_id, state)

    with unit_of_work(factory) as session:
        by_key = authenticate_api_key(session, key.token, NOW, hasher=HASHER)
        by_session = authenticate_session(session, web.token, NOW)

    assert (by_key is not None, by_session is not None) == (authenticates, authenticates)


# --- (d) bloqueio concorrente com login -------------------------------------------------------


def _wait_until_someone_waits_for_a_lock(factory: sessionmaker[Session]) -> None:
    deadline = time.monotonic() + LOCK_WAIT_DEADLINE_S
    with factory() as session:
        while time.monotonic() < deadline:
            waiting = session.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
            session.rollback()
            if waiting:
                return
            threading.Event().wait(0.01)
    raise AssertionError("a segunda transação não chegou a esperar a trava da conta")


def _in_thread(work: Callable[[], object]) -> tuple[threading.Thread, list[object]]:
    result: list[object] = []
    thread = threading.Thread(target=lambda: result.append(work()), daemon=True)
    thread.start()
    return thread, result


def _block(factory: sessionmaker[Session], user_id: UUID) -> bool:
    with unit_of_work(factory) as session:
        return block_account(session, user_id, NOW)


def _active_account(factory: sessionmaker[Session]) -> UUID:
    with unit_of_work(factory) as session:
        resolved = resolve_login(session, IDENTITY, signup_open=True, now=NOW)
    assert isinstance(resolved, UUID)
    return resolved


def test_bloqueio_durante_o_login_revoga_a_sessao_que_o_login_abriu(
    factory: sessionmaker[Session],
) -> None:
    user_id = _active_account(factory)
    with unit_of_work(factory) as login:
        assert resolve_login(login, IDENTITY, signup_open=True, now=NOW) == user_id
        blocker, _ = _in_thread(lambda: _block(factory, user_id))
        _wait_until_someone_waits_for_a_lock(factory)
        issued = start_session(
            login, user_id=user_id, authenticated_at=NOW, now=NOW, csrf_key=CSRF_KEY
        )
    blocker.join(LOCK_WAIT_DEADLINE_S)

    assert not blocker.is_alive()
    with factory() as session:
        revoked_at = session.execute(
            select(web_session.c.revoked_at).where(web_session.c.id == issued.session_id)
        ).scalar_one()
    assert revoked_at is not None
    # O defeito só apareceria depois da liberação: a sessão esquecida voltaria a valer.
    with unit_of_work(factory) as session:
        unblock_account(session, user_id)
    with unit_of_work(factory) as session:
        assert authenticate_session(session, issued.token, NOW + timedelta(minutes=1)) is None


def test_login_durante_o_bloqueio_e_recusado(factory: sessionmaker[Session]) -> None:
    user_id = _active_account(factory)

    def login() -> object:
        with unit_of_work(factory) as session:
            return resolve_login(session, IDENTITY, signup_open=True, now=NOW)

    with unit_of_work(factory) as blocking:
        block_account(blocking, user_id, NOW)
        attempt, result = _in_thread(login)
        _wait_until_someone_waits_for_a_lock(factory)
    attempt.join(LOCK_WAIT_DEADLINE_S)

    assert not attempt.is_alive()
    refusal = result[0]
    assert isinstance(refusal, LoginRefusal)
    assert refusal.reason in {LoginRefused.BLOCKED, LoginRefused.PENDING_APPROVAL}


# --- (e) revogação sem dono só na CLI do administrador ----------------------------------------


def test_revoke_key_sem_dono_so_e_chamada_pela_cli_do_administrador() -> None:
    """Fitness: a API revoga por `revoke_owned_key`, com o dono no WHERE (404 para alheia)."""
    callers = sorted(
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if "revoke_key(" in path.read_text() and path.name != "accounts.py"
    )

    assert callers == ["entrypoints/admin_cli.py"]


# --- (f) emissão contra o `logout-all` --------------------------------------------------------


def _issue(factory: sessionmaker[Session], user_id: UUID, web: UUID, now: datetime = NOW) -> object:
    with unit_of_work(factory) as session:
        return issue_key_for_session(
            session,
            owner_id=user_id,
            session_id=web,
            scopes=frozenset({Scope.RUNS_READ}),
            days=30,
            now=now,
        )


def _active_keys(factory: sessionmaker[Session], user_id: UUID) -> int:
    with factory() as session:
        count: int = session.execute(
            select(func.count())
            .select_from(api_key)
            .where(api_key.c.owner_id == user_id, api_key.c.revoked_at.is_(None))
        ).scalar_one()
    return count


def test_emissao_com_principal_de_antes_do_logout_all_e_recusada(
    factory: sessionmaker[Session],
) -> None:
    # O Principal foi lido numa transação; o `logout-all` confirmou antes da emissão começar.
    user_id = new_user(factory)
    web = new_web_session(factory, user_id, now=NOW)
    with unit_of_work(factory) as session:
        revoke_all_credentials(session, user_id, NOW)

    with pytest.raises(UnknownAccountError):
        _issue(factory, user_id, web)

    assert _active_keys(factory, user_id) == 0


def test_emissao_espera_o_logout_all_e_e_recusada(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    web = new_web_session(factory, user_id, now=NOW)

    with unit_of_work(factory) as logout:
        revoke_all_credentials(logout, user_id, NOW)
        emission = in_thread(lambda: _issue(factory, user_id, web))
        wait_until_someone_waits_for_a_lock(factory, "a emissão")

    with pytest.raises(UnknownAccountError):
        emission.result()
    assert _active_keys(factory, user_id) == 0


def test_emissao_com_sessao_de_outra_conta_e_recusada(factory: sessionmaker[Session]) -> None:
    user_id, other = new_user(factory), new_user(factory)
    others_session = new_web_session(factory, other, now=NOW)

    with pytest.raises(UnknownAccountError):
        _issue(factory, user_id, others_session)

    assert _active_keys(factory, user_id) == 0


def test_emissao_com_sessao_vencida_e_recusada(factory: sessionmaker[Session]) -> None:
    # Fronteira igual à de `resolve_session`: no instante do prazo a sessão já não vale. O prazo de
    # inatividade nunca passa do absoluto, então vencer um basta para cobrir os dois.
    user_id = new_user(factory)
    web = new_web_session(factory, user_id, now=NOW)

    with pytest.raises(UnknownAccountError):
        _issue(factory, user_id, web, NOW + timedelta(minutes=30))

    assert _active_keys(factory, user_id) == 0


# --- (g) trava da conta: FOR NO KEY UPDATE -----------------------------------------------------
# A emissão e o `logout-all` se enfileiram pela linha da conta, mas não mudam a chave dela. Com
# `FOR NO KEY UPDATE`, o `FOR KEY SHARE` das FKs (sessão nova no login, Run, auditoria) não espera.


def _account_locks(statements: list[str]) -> list[str]:
    return [sql for sql in statements if "FROM app_user" in sql and " FOR " in sql]


def test_emissao_trava_a_conta_com_for_no_key_update(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    web = new_web_session(factory, user_id, now=NOW)

    with unit_of_work(factory) as session, captured_sql(session) as statements:
        issue_key_for_session(
            session,
            owner_id=user_id,
            session_id=web,
            scopes=frozenset({Scope.RUNS_READ}),
            days=30,
            now=NOW,
        )

    [lock] = _account_locks(statements)
    assert lock.endswith("FOR NO KEY UPDATE")


def test_logout_all_trava_a_conta_com_for_no_key_update(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)

    with unit_of_work(factory) as session, captured_sql(session) as statements:
        revoke_all_credentials(session, user_id, NOW)

    [lock] = _account_locks(statements)
    assert lock.endswith("FOR NO KEY UPDATE")


def test_logout_all_em_andamento_nao_segura_o_login_da_mesma_conta(
    factory: sessionmaker[Session],
) -> None:
    user_id = new_user(factory)

    with unit_of_work(factory) as logout:
        revoke_all_credentials(logout, user_id, NOW)
        # A FK da sessão nova pega `FOR KEY SHARE` na conta; com `FOR UPDATE` ela esperaria o
        # `logout-all` e o `lock_timeout` estouraria.
        with unit_of_work(factory) as login:
            login.execute(text("SET LOCAL lock_timeout = '1s'"))
            new_session = new_web_session_in(login, user_id)

    assert new_session is not None


def new_web_session_in(session: Session, user_id: UUID) -> UUID:
    """Linha de sessão gravada direto, sem `start_session`: só a FK para a conta trava."""
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
