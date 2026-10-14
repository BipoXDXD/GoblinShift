"""Estado transitório do login no PostgreSQL (docs/GoblinShiftPlan.md, 3.6; ADR 024).

O Authlib guarda `state`, `nonce`, `code_verifier` e `redirect_uri` no cache enquanto o usuário
está no provedor; o cookie `__Secure-gs_login` leva só o marcador que prova que o callback vem do
mesmo navegador. O estado vive no máximo 10 minutos e é lido uma única vez: a leitura o apaga
na mesma instrução, então um `state` repetido (ou dois callbacks simultâneos) não reaproveita o
estado de outro.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from anyio import to_thread
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity.tables import login_state

# O mesmo prazo do cookie de login (LOGIN_COOKIE_MAX_AGE_S).
LOGIN_STATE_TTL = timedelta(minutes=10)
# Os mesmos limites dos CHECKs de `login_state` (migração 0006).
MAX_KEY_CHARS = 200


def save_state(session: Session, key: str, value: str, expires_at: datetime) -> None:
    """Grava ou substitui o estado da chave."""
    statement = insert(login_state).values(key=key, value=value, expires_at=expires_at)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[login_state.c.key],
            set_={"value": statement.excluded.value, "expires_at": statement.excluded.expires_at},
        )
    )


def take_state(session: Session, key: str, now: datetime) -> str | None:
    """Remove e devolve o estado ainda no prazo; vencido, desconhecido ou já lido → None."""
    if not _storable_key(key):
        return None
    statement = (
        delete(login_state)
        .where(login_state.c.key == key)
        .returning(login_state.c.value, login_state.c.expires_at)
    )
    row = session.execute(statement).first()
    if row is None or row.expires_at <= now:
        return None
    value: str = row.value
    return value


def delete_state(session: Session, key: str) -> None:
    """Apaga o estado; chave inexistente já está no estado final."""
    if _storable_key(key):
        session.execute(delete(login_state).where(login_state.c.key == key))


def purge_expired_states(session: Session, now: datetime, *, limit: int) -> int:
    """Apaga até `limit` estados vencidos (logins abandonados); devolve quantos apagou."""
    expired = (
        select(login_state.c.key)
        .where(login_state.c.expires_at <= now)
        .order_by(login_state.c.expires_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    statement = delete(login_state).where(login_state.c.key.in_(expired))
    return len(session.execute(statement.returning(login_state.c.key)).all())


def _storable_key(key: str) -> bool:
    """O `state` vem da query string do callback: NUL ou tamanho fora do CHECK não é chave."""
    return 0 < len(key) <= MAX_KEY_CHARS and "\x00" not in key


class PostgresLoginStateCache:
    """Cache do Authlib (`get`, `set`, `delete` assíncronos) sobre `login_state`.

    Cada operação é uma transação curta numa thread do pool, porque o projeto é síncrono e a
    integração Starlette do Authlib é assíncrona.
    """

    def __init__(
        self,
        factory: sessionmaker[Session],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._factory = factory
        self._clock = clock

    async def get(self, key: str) -> str | None:
        return await to_thread.run_sync(self._take, key)

    async def set(self, key: str, value: str, expires_in: int | None = None) -> None:
        await to_thread.run_sync(self._save, key, value, expires_in)

    async def delete(self, key: str) -> None:
        await to_thread.run_sync(self._delete, key)

    def _take(self, key: str) -> str | None:
        with self._factory() as session, session.begin():
            return take_state(session, key, self._clock())

    def _save(self, key: str, value: str, expires_in: int | None) -> None:
        ttl = LOGIN_STATE_TTL
        if expires_in is not None:
            ttl = min(ttl, timedelta(seconds=expires_in))
        with self._factory() as session, session.begin():
            save_state(session, key, value, self._clock() + ttl)

    def _delete(self, key: str) -> None:
        with self._factory() as session, session.begin():
            delete_state(session, key)
