"""Migrações 0020 e 0021: candidato amarrado à tentativa (#442, 23a e 23b).

A 0020 cria a FK de `stored_object.attempt_id` e a CHECK do par `attempt_id`/`generation` sem
validar; a 0021 repara o legado e as valida. O legado é semeado na 0019, antes das constraints.
"""

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine, text

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations

pytestmark = pytest.mark.integration

CONSTRAINTS = ["stored_object_attempt_id_fkey", "stored_object_attempt_generation_check"]
OBJECTS = ["orphan", "divergent", "no_generation", "generation_only", "intact"]


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head")


def at_0019(settings: Settings) -> None:
    """Leva o schema a 0019 venha ele de baixo (banco novo) ou de cima (head)."""
    run_migrations(settings, "0019")
    downgrade_migrations(settings, "0019")


def new_attempt(conn: Connection, generation: int) -> tuple[UUID, UUID]:
    """Conta nova com uma Run terminal e uma tentativa encerrada; devolve `(owner, attempt_id)`."""
    owner: UUID = conn.execute(
        text(
            "INSERT INTO app_user (issuer, subject, display_name) "
            "VALUES ('teste', gen_random_uuid()::text, 'Ana') RETURNING id"
        )
    ).scalar_one()
    run_id = conn.execute(
        text(
            "INSERT INTO run (owner_id, recipe_id, recipe_version, executor_digest, limits, "
            "status, error_code, queue_expires_at, finished_at) "
            "VALUES (:owner, 'csv-janitor', 1, 'sha256:0', '{}', 'failed', 'INFRA_POISON', "
            "now(), now()) RETURNING id"
        ),
        {"owner": owner},
    ).scalar_one()
    attempt_id: UUID = conn.execute(
        text(
            "INSERT INTO attempt (run_id, number, generation, container_name, lease_expires_at, "
            "ended_at) VALUES (:run, 1, :generation, gen_random_uuid()::text, now(), now()) "
            "RETURNING id"
        ),
        {"run": run_id, "generation": generation},
    ).scalar_one()
    return owner, attempt_id


def new_object(
    conn: Connection, owner: UUID, attempt_id: UUID | None, generation: int | None
) -> UUID:
    object_id: UUID = conn.execute(
        text(
            "INSERT INTO stored_object (owner_id, storage_key, attempt_id, generation) "
            "VALUES (:owner, gen_random_uuid()::text, :attempt, :generation) RETURNING id"
        ),
        {"owner": owner, "attempt": attempt_id, "generation": generation},
    ).scalar_one()
    return object_id


def pairs(engine: Engine, objects: list[UUID]) -> dict[UUID, tuple[UUID | None, int | None]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, attempt_id, generation FROM stored_object WHERE id = ANY(:objects)"),
            {"objects": objects},
        ).all()
    return {row.id: (row.attempt_id, row.generation) for row in rows}


def validated(engine: Engine) -> dict[str, bool]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT conname, convalidated FROM pg_constraint WHERE conname = ANY(:names)"),
            {"names": CONSTRAINTS},
        ).all()
    return {row.conname: row.convalidated for row in rows}


def seed_legacy(settings: Settings, engine: Engine) -> dict[str, UUID]:
    """Na 0019, sem FK nem CHECK: um objeto por regra do reparo e um já correto."""
    at_0019(settings)
    missing_attempt = uuid4()
    with engine.begin() as conn:
        owner, attempt_id = new_attempt(conn, generation=2)
        return {
            "attempt": attempt_id,
            "missing_attempt": missing_attempt,
            "orphan": new_object(conn, owner, missing_attempt, 1),
            "divergent": new_object(conn, owner, attempt_id, 1),
            "no_generation": new_object(conn, owner, attempt_id, None),
            "generation_only": new_object(conn, owner, None, 3),
            "intact": new_object(conn, owner, attempt_id, 2),
        }


def test_0020_cria_as_constraints_sem_validar_nem_mexer_no_legado(
    db_settings: Settings, engine: Engine
) -> None:
    seeded = seed_legacy(db_settings, engine)

    run_migrations(db_settings, "0020")

    assert validated(engine) == dict.fromkeys(CONSTRAINTS, False)
    attempt_id = seeded["attempt"]
    assert pairs(engine, [seeded[name] for name in OBJECTS]) == {
        seeded["orphan"]: (seeded["missing_attempt"], 1),
        seeded["divergent"]: (attempt_id, 1),
        seeded["no_generation"]: (attempt_id, None),
        seeded["generation_only"]: (None, 3),
        seeded["intact"]: (attempt_id, 2),
    }


def test_0021_anula_par_orfao_ou_divergente_e_completa_a_geracao(
    db_settings: Settings, engine: Engine
) -> None:
    seeded = seed_legacy(db_settings, engine)

    run_migrations(db_settings, "0021")

    attempt_id = seeded["attempt"]
    assert pairs(engine, [seeded[name] for name in OBJECTS]) == {
        seeded["orphan"]: (None, None),
        seeded["divergent"]: (None, None),
        seeded["no_generation"]: (attempt_id, 2),
        seeded["generation_only"]: (None, None),
        seeded["intact"]: (attempt_id, 2),
    }
    assert validated(engine) == dict.fromkeys(CONSTRAINTS, True)
