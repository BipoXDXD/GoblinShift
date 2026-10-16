"""Migração 0014: papel `preview` e `artifact.source_artifact_id` (pré-visualização, 2.2 e 3.6).

Expand: o CHECK de `role` aceita `preview`, e a coluna nova liga o preview à saída de que deriva
por FK composta ao dono, em cascata. O downgrade apaga os previews (o CHECK antigo não os aceita)
e pede a remoção dos objetos que só eles apontavam, pelo mesmo `delete_requested` que a purga da
manutenção já atende; sem isso o arquivo ficaria `committed` sem referência, ocupando a cota.
"""

from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations

pytestmark = pytest.mark.integration


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head")


def new_user(conn: Connection) -> UUID:
    user_id: UUID = conn.execute(
        text(
            "INSERT INTO app_user (issuer, subject, display_name) "
            "VALUES ('teste', gen_random_uuid()::text, 'Ana') RETURNING id"
        )
    ).scalar_one()
    return user_id


def new_object(conn: Connection, owner: UUID) -> UUID:
    object_id: UUID = conn.execute(
        text(
            "INSERT INTO stored_object (owner_id, storage_key, state, sha256, size_bytes, "
            "committed_at) VALUES (:owner, gen_random_uuid()::text, 'committed', "
            "decode(repeat('00', 32), 'hex'), 1, now()) RETURNING id"
        ),
        {"owner": owner},
    ).scalar_one()
    return object_id


def new_artifact(
    conn: Connection,
    owner: UUID,
    object_id: UUID,
    role: str = "output",
    source: UUID | None = None,
) -> UUID:
    artifact_id: UUID = conn.execute(
        text(
            "INSERT INTO artifact (owner_id, object_id, role, file_name, expires_at, "
            "source_artifact_id) VALUES (:owner, :object, :role, 'a.dat', "
            "now() + interval '48 hours', :source) RETURNING id"
        ),
        {"owner": owner, "object": object_id, "role": role, "source": source},
    ).scalar_one()
    return artifact_id


def object_state(conn: Connection, object_id: UUID) -> tuple[str, bool]:
    row = conn.execute(
        text(
            "SELECT state, delete_requested_at IS NOT NULL AS requested "
            "FROM stored_object WHERE id = :id"
        ),
        {"id": object_id},
    ).one()
    return row.state, row.requested


def artifact_count(conn: Connection, artifact_id: UUID) -> int:
    count: int = conn.execute(
        text("SELECT count(*) FROM artifact WHERE id = :id"), {"id": artifact_id}
    ).scalar_one()
    return count


def test_preview_de_artefato_de_outra_conta_e_recusado(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0014")
    with engine.begin() as conn:
        owner, other = new_user(conn), new_user(conn)
        source = new_artifact(conn, owner, new_object(conn, owner))
        foreign_object = new_object(conn, other)

    with pytest.raises(IntegrityError), engine.begin() as conn:
        new_artifact(conn, other, foreign_object, role="preview", source=source)


def test_downgrade_apaga_o_preview_e_pede_a_remocao_do_objeto_dele(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0014")
    with engine.begin() as conn:
        owner = new_user(conn)
        source_object = new_object(conn, owner)
        source = new_artifact(conn, owner, source_object)
        preview_object = new_object(conn, owner)
        preview = new_artifact(conn, owner, preview_object, role="preview", source=source)

    downgrade_migrations(db_settings, "0013")

    with engine.connect() as conn:
        assert artifact_count(conn, preview) == 0
        assert object_state(conn, preview_object) == ("delete_requested", True)
        # A saída de que o preview derivava não é tocada.
        assert artifact_count(conn, source) == 1
        assert object_state(conn, source_object) == ("committed", False)


def test_downgrade_mantem_o_objeto_que_outro_artefato_valido_aponta(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0014")
    with engine.begin() as conn:
        owner = new_user(conn)
        shared = new_object(conn, owner)
        source = new_artifact(conn, owner, shared)
        preview = new_artifact(conn, owner, shared, role="preview", source=source)

    downgrade_migrations(db_settings, "0013")

    with engine.connect() as conn:
        assert artifact_count(conn, preview) == 0
        assert object_state(conn, shared) == ("committed", False)


def test_downgrade_nao_reabre_objeto_de_preview_ja_removido(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0014")
    with engine.begin() as conn:
        owner = new_user(conn)
        source = new_artifact(conn, owner, new_object(conn, owner))
        removed = new_object(conn, owner)
        new_artifact(conn, owner, removed, role="preview", source=source)
        conn.execute(
            text("UPDATE stored_object SET state = 'deleted' WHERE id = :id"), {"id": removed}
        )

    downgrade_migrations(db_settings, "0013")

    with engine.connect() as conn:
        assert object_state(conn, removed) == ("deleted", False)
