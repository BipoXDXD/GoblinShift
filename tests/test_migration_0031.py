"""Migração 0031: `Idempotency-Key` na emissão de chave e janela do limite por canal (#442).

`api_key.idem_key` (57b). Expand: coluna anulável e sem default, que o código anterior não grava;
as chaves existentes ficam com `idem_key` nulo. Um CHECK confere o formato de `runs.acceptance`,
e o índice único parcial `(owner_id, idem_key)` impede duas chaves da mesma conta com a mesma
`Idempotency-Key`. O downgrade remove a coluna e, se alguma chave tiver `idem_key`, só roda com
`GS_ALLOW_LOSSY_DOWNGRADE=1`.

`channel_request_window` (49a). Expand: tabela nova, com o canal na PK; a `request_window` da 0007
fica intacta, e o `INSERT … ON CONFLICT` da réplica anterior continua valendo nela durante o
deploy. O downgrade apaga a tabela nova e, se ela tiver janelas, só roda com a mesma licença.
"""

from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations

pytestmark = pytest.mark.integration

IDEM = "repeticao-do-cliente-0001"


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head")


def new_user(engine: Engine) -> UUID:
    with engine.begin() as conn:
        user_id: UUID = conn.execute(
            text(
                "INSERT INTO app_user (issuer, subject, display_name) "
                "VALUES ('teste', gen_random_uuid()::text, 'Ana') RETURNING id"
            )
        ).scalar_one()
    return user_id


# O INSERT do código anterior à 0031, que não cita `idem_key`, e o do código novo.
_INSERT_WITHOUT_IDEM = text(
    "INSERT INTO api_key (owner_id, public_id, secret_hash, scopes, expires_at)"
    " VALUES (:owner_id, substr(md5(random()::text), 1, 16), sha256(random()::text::bytea),"
    " ARRAY['files:read'], now() + interval '1 day') RETURNING id"
)
_INSERT_WITH_IDEM = text(
    "INSERT INTO api_key (owner_id, public_id, secret_hash, scopes, expires_at, idem_key)"
    " VALUES (:owner_id, substr(md5(random()::text), 1, 16), sha256(random()::text::bytea),"
    " ARRAY['files:read'], now() + interval '1 day', :idem_key) RETURNING id"
)


def new_key(engine: Engine, owner_id: UUID, idem_key: str | None = None) -> UUID:
    statement = _INSERT_WITHOUT_IDEM if idem_key is None else _INSERT_WITH_IDEM
    with engine.begin() as conn:
        key_id: UUID = conn.execute(
            statement, {"owner_id": owner_id, "idem_key": idem_key}
        ).scalar_one()
    return key_id


def idem_key_of(engine: Engine, key_id: UUID) -> str | None:
    with engine.connect() as conn:
        stored: str | None = conn.execute(
            text("SELECT idem_key FROM api_key WHERE id = :id"), {"id": key_id}
        ).scalar_one()
    return stored


def current_revision(engine: Engine) -> str:
    with engine.connect() as conn:
        revision: str = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    return revision


def api_key_columns(engine: Engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns("api_key")}


def has_table(engine: Engine, name: str) -> bool:
    return inspect(engine).has_table(name)


def clear_lossy_rows(engine: Engine) -> None:
    """O container é da sessão: outros testes podem ter deixado `idem_key` e janelas por canal."""
    with engine.begin() as conn:
        conn.execute(text("UPDATE api_key SET idem_key = NULL WHERE idem_key IS NOT NULL"))
        conn.execute(text("DELETE FROM channel_request_window"))


# O INSERT da réplica anterior à 0031 (`rate_limit.count_request` da 0007), que segue servindo
# depois do `migrate` até o deploy terminar.
_OLD_COUNT = text(
    "INSERT INTO request_window (owner_id, request_class, window_start, request_count)"
    " VALUES (:owner_id, 'read', date_trunc('minute', now()), 1)"
    " ON CONFLICT (owner_id, request_class, window_start)"
    " DO UPDATE SET request_count = request_window.request_count + 1"
    " RETURNING request_count"
)
_NEW_COUNT = text(
    "INSERT INTO channel_request_window"
    " (owner_id, channel, request_class, window_start, request_count)"
    " VALUES (:owner_id, :channel, 'read', date_trunc('minute', now()), 1)"
    " ON CONFLICT (owner_id, channel, request_class, window_start)"
    " DO UPDATE SET request_count = channel_request_window.request_count + 1"
    " RETURNING request_count"
)


def count_new(engine: Engine, owner_id: UUID, channel: str) -> int:
    with engine.begin() as conn:
        counted: int = conn.execute(
            _NEW_COUNT, {"owner_id": owner_id, "channel": channel}
        ).scalar_one()
    return counted


def test_upgrade_mantem_as_chaves_existentes_com_idem_key_nulo(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0031")
    downgrade_migrations(db_settings, "0030")
    key_id = new_key(engine, new_user(engine))

    run_migrations(db_settings, "0031")

    assert idem_key_of(engine, key_id) is None


def test_insert_sem_idem_key_do_codigo_anterior_continua_valendo(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0031")
    owner_id = new_user(engine)

    first, second = new_key(engine, owner_id), new_key(engine, owner_id)

    assert idem_key_of(engine, first) is idem_key_of(engine, second) is None


@pytest.mark.parametrize(
    "idem_key",
    ["a" * 15, "a" * 65, "com espaco-0000000", "a" * 16 + "\x01"],
    ids=["15", "65", "espaco", "controle"],
)
def test_idem_key_fora_do_formato_e_recusada(
    db_settings: Settings, engine: Engine, idem_key: str
) -> None:
    run_migrations(db_settings, "0031")

    with pytest.raises(IntegrityError, match="api_key_idem_key_check"):
        new_key(engine, new_user(engine), idem_key)


def test_mesma_idem_key_na_mesma_conta_e_recusada(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0031")
    owner_id = new_user(engine)
    new_key(engine, owner_id, IDEM)

    with pytest.raises(IntegrityError, match="api_key_owner_id_idem_key_idx"):
        new_key(engine, owner_id, IDEM)


def test_mesma_idem_key_em_outra_conta_e_aceita(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0031")
    new_key(engine, new_user(engine), IDEM)

    other = new_key(engine, new_user(engine), IDEM)

    assert idem_key_of(engine, other) == IDEM


def test_downgrade_com_idem_key_sem_licenca_aborta_e_mantem_a_coluna(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0031")
    key_id = new_key(engine, new_user(engine), IDEM)

    with pytest.raises(DBAPIError, match="GS_ALLOW_LOSSY_DOWNGRADE"):
        downgrade_migrations(db_settings, "0030", allow_lossy=False)

    assert current_revision(engine) == "0031"
    assert idem_key_of(engine, key_id) == IDEM


def test_downgrade_com_licenca_remove_a_coluna_e_mantem_a_chave(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0031")
    key_id = new_key(engine, new_user(engine), IDEM)

    downgrade_migrations(db_settings, "0030", allow_lossy=True)

    assert current_revision(engine) == "0030"
    assert "idem_key" not in api_key_columns(engine)
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM api_key WHERE id = :id"), {"id": key_id}
            ).scalar_one()
            == 1
        )


def test_downgrade_sem_idem_key_desce_sem_licenca(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0031")
    clear_lossy_rows(engine)

    downgrade_migrations(db_settings, "0030", allow_lossy=False)

    assert current_revision(engine) == "0030"
    assert "idem_key" not in api_key_columns(engine)
    assert not has_table(engine, "channel_request_window")


# --- Janela do limite por canal (49a) ---------------------------------------------------------


def test_insert_da_replica_anterior_continua_valendo_na_tabela_antiga(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0031")
    owner_id = new_user(engine)

    with engine.begin() as conn:
        first = conn.execute(_OLD_COUNT, {"owner_id": owner_id}).scalar_one()
        second = conn.execute(_OLD_COUNT, {"owner_id": owner_id}).scalar_one()

    assert (first, second) == (1, 2)


def test_canais_da_mesma_conta_tem_janelas_proprias(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0031")
    owner_id = new_user(engine)

    counts = [count_new(engine, owner_id, channel) for channel in ("api_key", "api_key", "session")]

    assert counts == [1, 2, 1]


def test_canal_desconhecido_e_recusado(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0031")

    with pytest.raises(IntegrityError, match="channel_request_window_channel_check"):
        count_new(engine, new_user(engine), "cookie")


def test_upgrade_downgrade_upgrade_com_a_guarda_das_janelas_por_canal(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0031")
    clear_lossy_rows(engine)
    owner_id = new_user(engine)
    count_new(engine, owner_id, "api_key")

    with pytest.raises(DBAPIError, match="janelas do limite por canal"):
        downgrade_migrations(db_settings, "0030", allow_lossy=False)
    assert current_revision(engine) == "0031"
    assert has_table(engine, "channel_request_window")

    downgrade_migrations(db_settings, "0030", allow_lossy=True)
    assert current_revision(engine) == "0030"
    assert not has_table(engine, "channel_request_window")
    assert has_table(engine, "request_window")

    run_migrations(db_settings, "0031")
    assert count_new(engine, owner_id, "api_key") == 1
