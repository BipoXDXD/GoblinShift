"""Migração 0032: quem agiu (`audit_event.actor`) e os eventos novos do `gs-admin` (#442, 50c).

Expand: coluna anulável e sem default, que o código anterior não grava; os eventos existentes
ficam com `actor` nulo. Um CHECK limita `actor` a `user`, `admin` e `system`, e o CHECK do tipo
ganha `ACCOUNT_CREATED`, `ACCOUNT_LOOKED_UP` e `ACCOUNT_APPROVED`. O downgrade apaga os eventos
dos tipos novos e a coluna e, se houver algum desses dados, só roda com
`GS_ALLOW_LOSSY_DOWNGRADE=1`.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations

pytestmark = pytest.mark.integration

# O controlador renumera no merge (R1 do preflight): os testes citam só estas constantes.
REVISION = "0032"
PREVIOUS = "0030"
NEW_TYPES = ("ACCOUNT_CREATED", "ACCOUNT_LOOKED_UP", "ACCOUNT_APPROVED")


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head")


def record(engine: Engine, event_type: str, actor: str | None = None) -> int:
    with engine.begin() as conn:
        if actor is None:
            # O INSERT do código anterior à 0032, que não cita `actor`.
            statement = text(
                "INSERT INTO audit_event (event_type, outcome) "
                "VALUES (:event_type, 'success') RETURNING id"
            )
        else:
            statement = text(
                "INSERT INTO audit_event (event_type, outcome, actor) "
                "VALUES (:event_type, 'success', :actor) RETURNING id"
            )
        event_id: int = conn.execute(
            statement, {"event_type": event_type, "actor": actor}
        ).scalar_one()
    return event_id


def actor_of(engine: Engine, event_id: int) -> str | None:
    with engine.connect() as conn:
        actor: str | None = conn.execute(
            text("SELECT actor FROM audit_event WHERE id = :id"), {"id": event_id}
        ).scalar_one()
    return actor


def count(engine: Engine, event_type: str) -> int:
    with engine.connect() as conn:
        found: int = conn.execute(
            text("SELECT count(*) FROM audit_event WHERE event_type = :event_type"),
            {"event_type": event_type},
        ).scalar_one()
    return found


def current_revision(engine: Engine) -> str:
    with engine.connect() as conn:
        revision: str = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    return revision


def audit_columns(engine: Engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns("audit_event")}


def forget_lossy_rows(engine: Engine) -> None:
    # O container é da sessão: outros testes deixam eventos com `actor` e dos tipos novos.
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM audit_event WHERE actor IS NOT NULL OR event_type = ANY(:types)"),
            {"types": list(NEW_TYPES)},
        )


def test_evento_anterior_a_migracao_fica_com_actor_nulo(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, REVISION)
    forget_lossy_rows(engine)
    downgrade_migrations(db_settings, PREVIOUS, allow_lossy=False)
    old_event = record(engine, "ACCOUNT_DELETION_REQUESTED")

    run_migrations(db_settings, REVISION)

    assert actor_of(engine, old_event) is None


def test_insert_sem_actor_do_codigo_anterior_continua_valendo(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, REVISION)

    event_id = record(engine, "LOGOUT")

    assert actor_of(engine, event_id) is None


@pytest.mark.parametrize("actor", ["user", "admin", "system"])
def test_actor_da_lista_e_aceito(db_settings: Settings, engine: Engine, actor: str) -> None:
    run_migrations(db_settings, REVISION)

    event_id = record(engine, "LOGOUT", actor)

    assert actor_of(engine, event_id) == actor


@pytest.mark.parametrize("actor", ["root", "", "Admin"])
def test_actor_fora_da_lista_e_recusado(db_settings: Settings, engine: Engine, actor: str) -> None:
    run_migrations(db_settings, REVISION)

    with pytest.raises(IntegrityError, match="audit_event_actor_check"):
        record(engine, "LOGOUT", actor)


@pytest.mark.parametrize("event_type", NEW_TYPES)
def test_tipos_novos_sao_aceitos(db_settings: Settings, engine: Engine, event_type: str) -> None:
    run_migrations(db_settings, REVISION)
    before = count(engine, event_type)

    record(engine, event_type, "admin")

    assert count(engine, event_type) == before + 1


def test_downgrade_com_dado_novo_sem_licenca_aborta_e_mantem_tudo(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, REVISION)
    event_id = record(engine, "ACCOUNT_CREATED", "admin")

    with pytest.raises(DBAPIError, match="GS_ALLOW_LOSSY_DOWNGRADE"):
        downgrade_migrations(db_settings, PREVIOUS, allow_lossy=False)

    assert current_revision(engine) == REVISION
    assert actor_of(engine, event_id) == "admin"


def test_downgrade_com_licenca_apaga_os_tipos_novos_e_a_coluna_e_restaura_o_check(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, REVISION)
    record(engine, "ACCOUNT_APPROVED", "admin")
    record(engine, "ACCOUNT_BLOCKED", "admin")
    blocked = count(engine, "ACCOUNT_BLOCKED")

    downgrade_migrations(db_settings, PREVIOUS, allow_lossy=True)

    assert current_revision(engine) == PREVIOUS
    assert "actor" not in audit_columns(engine)
    assert all(count(engine, event_type) == 0 for event_type in NEW_TYPES)
    assert count(engine, "ACCOUNT_BLOCKED") == blocked
    with (
        pytest.raises(IntegrityError, match="audit_event_event_type_check"),
        engine.begin() as conn,
    ):
        conn.execute(
            text(
                "INSERT INTO audit_event (event_type, outcome) "
                "VALUES ('ACCOUNT_CREATED', 'success')"
            )
        )


def test_downgrade_sem_dado_novo_desce_sem_licenca(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, REVISION)
    forget_lossy_rows(engine)
    record(engine, "LOGOUT")

    downgrade_migrations(db_settings, PREVIOUS, allow_lossy=False)

    assert current_revision(engine) == PREVIOUS
    assert "actor" not in audit_columns(engine)
