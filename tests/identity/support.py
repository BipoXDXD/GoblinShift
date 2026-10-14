"""Banco limpo e contas prontas para os testes de identidade e da API."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Engine, text, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity import IssuedKey, Scope, create_admin_user, issue_key
from goblinshift.identity.tables import app_user
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.settings import Settings
from tests.conftest import restore_capacity_seed, run_migrations

ALL_SCOPES = frozenset(Scope)


def prepare_database(settings: Settings, engine: Engine) -> None:
    """Schema no head, tabelas de identidade vazias (com o que depende delas) e o pool global."""
    run_migrations(settings, "head")
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE app_user, api_key, audit_event, login_state CASCADE"))
        restore_capacity_seed(conn)


def new_user(factory: sessionmaker[Session], *, blocked: bool = False) -> UUID:
    with unit_of_work(factory) as session:
        user_id = create_admin_user(session, "Conta de teste")
        if blocked:
            session.execute(
                update(app_user).where(app_user.c.id == user_id).values(status="blocked")
            )
    return user_id


def new_key(
    factory: sessionmaker[Session],
    user_id: UUID,
    *,
    scopes: frozenset[Scope] = ALL_SCOPES,
    now: datetime | None = None,
    days: int = 30,
) -> IssuedKey:
    with unit_of_work(factory) as session:
        return issue_key(
            session,
            owner_id=user_id,
            scopes=scopes,
            now=now or datetime.now(UTC),
            days=days,
        )
