"""Downgrade que apagaria dado aborta sem licença (#442, 47c e 23f).

Sete revisões desfazem o expand apagando linhas: os aceites dos Termos (0008), os overrides de
cota (0011), os presets (0012), o detalhe por item (0013), os previews (0014), os eventos de
mudança de limites (0016) e a marca de conta pendente, que o upgrade só reconstrói por
heurística (0018). Com alguma dessas linhas no banco, o `downgrade()` aborta numa guarda SQL,
que também sai no script do `--sql` offline; `GS_ALLOW_LOSSY_DOWNGRADE=1` aceita a perda. Sem
linha a perder, desce sem licença.
"""

from collections.abc import Callable, Iterator
from typing import NamedTuple
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import _alembic_env, downgrade_migrations, run_migrations

pytestmark = pytest.mark.integration

type Chave = UUID | int


class Caso(NamedTuple):
    revisao: str
    anterior: str
    semear: Callable[[Connection], Chave]
    contar: Callable[[Connection, Chave], int]
    rotulo: str | None = None


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head", allow_lossy=True)


def na_revisao(settings: Settings, revisao: str) -> None:
    """Leva o schema a `revisao` venha ele de baixo (banco novo) ou de cima (head)."""
    run_migrations(settings, revisao)
    downgrade_migrations(settings, revisao)


def current_revision(engine: Engine) -> str:
    with engine.connect() as conn:
        revision: str = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    return revision


def _escalar(conn: Connection, sql: str, **params: object) -> Chave:
    chave: Chave = conn.execute(text(sql), params).scalar_one()
    return chave


def _contar(conn: Connection, sql: str, chave: Chave) -> int:
    total: int = conn.execute(text(sql), {"chave": chave}).scalar_one()
    return total


def _conta(conn: Connection) -> Chave:
    return _escalar(
        conn,
        "INSERT INTO app_user (issuer, subject, display_name) "
        "VALUES ('teste', gen_random_uuid()::text, 'Ana') RETURNING id",
    )


def _objeto(conn: Connection, dono: Chave) -> Chave:
    return _escalar(
        conn,
        "INSERT INTO stored_object (owner_id, storage_key, state, sha256, size_bytes, "
        "committed_at) VALUES (:dono, gen_random_uuid()::text, 'committed', "
        "decode(repeat('00', 32), 'hex'), 1, now()) RETURNING id",
        dono=dono,
    )


def semear_aceite(conn: Connection) -> Chave:
    return _escalar(
        conn,
        "INSERT INTO audit_event (event_type, outcome) "
        "VALUES ('TERMS_ACCEPTED', 'success') RETURNING id",
    )


def semear_cota(conn: Connection) -> Chave:
    dono = _conta(conn)
    return _escalar(
        conn,
        "INSERT INTO account_quota (owner_id, runs_per_24h) VALUES (:dono, 5) RETURNING owner_id",
        dono=dono,
    )


def semear_preset(conn: Connection) -> Chave:
    return _escalar(
        conn,
        "INSERT INTO preset (owner_id, name, recipe_id, recipe_version, parameters) "
        "VALUES (:dono, 'limpeza', 'csv-janitor', 1, '{}') RETURNING id",
        dono=_conta(conn),
    )


def semear_detalhe(conn: Connection) -> Chave:
    dono = _conta(conn)
    upload = _escalar(
        conn,
        "INSERT INTO upload (owner_id, object_id, original_name, expected_size, state, "
        "expires_at) VALUES (:dono, :objeto, 'clientes.csv', 1, 'ready', "
        "now() + interval '1 hour') RETURNING id",
        dono=dono,
        objeto=_objeto(conn, dono),
    )
    run = _escalar(
        conn,
        "INSERT INTO run (owner_id, recipe_id, recipe_version, executor_digest, parameters, "
        "limits, status, queue_expires_at, finished_at) VALUES (:dono, 'csv-janitor', 1, "
        "'local-dev', '{}', '{}', 'succeeded', now(), now()) RETURNING id",
        dono=dono,
    )
    conn.execute(
        text(
            "INSERT INTO run_input (run_id, position, owner_id, upload_id, outcome, details) "
            "VALUES (:run, 0, :dono, :upload, 'ok', '{}'::jsonb)"
        ),
        {"run": run, "dono": dono, "upload": upload},
    )
    return run


def semear_preview(conn: Connection) -> Chave:
    dono = _conta(conn)
    saida = _escalar(
        conn,
        "INSERT INTO artifact (owner_id, object_id, role, file_name, expires_at) "
        "VALUES (:dono, :objeto, 'output', 'a.dat', now() + interval '48 hours') RETURNING id",
        dono=dono,
        objeto=_objeto(conn, dono),
    )
    return _escalar(
        conn,
        "INSERT INTO artifact (owner_id, object_id, role, file_name, expires_at, "
        "source_artifact_id) VALUES (:dono, :objeto, 'preview', 'a.png', "
        "now() + interval '48 hours', :saida) RETURNING id",
        dono=dono,
        objeto=_objeto(conn, dono),
        saida=saida,
    )


def semear_mudanca_de_limites(conn: Connection) -> Chave:
    return _escalar(
        conn,
        "INSERT INTO audit_event (event_type, outcome, details) VALUES "
        "('ACCOUNT_LIMITS_CHANGED', 'success', "
        '\'{"changes": {"runs_per_24h": {"old": null, "new": 5}}}\') RETURNING id',
    )


def semear_pendente(conn: Connection) -> Chave:
    return _escalar(
        conn,
        "INSERT INTO app_user (issuer, subject, display_name, status, awaiting_approval) "
        "VALUES ('teste', gen_random_uuid()::text, 'Ana', 'blocked', true) RETURNING id",
    )


def semear_bloqueada(conn: Connection) -> Chave:
    # Bloqueada, sem sessão e sem ACCOUNT_BLOCKED: o backfill do re-upgrade a marcaria pendente.
    # Sem nenhuma conta pendente no banco, a guarda só pode abortar pela bloqueada; os outros
    # testes da sessão deixam contas pendentes commitadas.
    conn.execute(text("UPDATE app_user SET awaiting_approval = false WHERE awaiting_approval"))
    return _escalar(
        conn,
        "INSERT INTO app_user (issuer, subject, display_name, status, awaiting_approval) "
        "VALUES ('teste', gen_random_uuid()::text, 'Ana', 'blocked', false) RETURNING id",
    )


CASOS = [
    Caso(
        "0008",
        "0007",
        semear_aceite,
        lambda conn, chave: _contar(
            conn,
            "SELECT count(*) FROM audit_event WHERE id = :chave AND event_type = 'TERMS_ACCEPTED'",
            chave,
        ),
    ),
    Caso(
        "0011",
        "0010",
        semear_cota,
        lambda conn, chave: _contar(
            conn, "SELECT count(*) FROM account_quota WHERE owner_id = :chave", chave
        ),
    ),
    Caso(
        "0012",
        "0011",
        semear_preset,
        lambda conn, chave: _contar(conn, "SELECT count(*) FROM preset WHERE id = :chave", chave),
    ),
    Caso(
        "0013",
        "0012",
        semear_detalhe,
        lambda conn, chave: _contar(
            conn,
            "SELECT count(*) FROM run_input WHERE run_id = :chave AND details IS NOT NULL",
            chave,
        ),
    ),
    Caso(
        "0014",
        "0013",
        semear_preview,
        lambda conn, chave: _contar(
            conn, "SELECT count(*) FROM artifact WHERE id = :chave AND role = 'preview'", chave
        ),
    ),
    Caso(
        "0016",
        "0015",
        semear_mudanca_de_limites,
        lambda conn, chave: _contar(
            conn,
            "SELECT count(*) FROM audit_event "
            "WHERE id = :chave AND event_type = 'ACCOUNT_LIMITS_CHANGED'",
            chave,
        ),
    ),
    Caso(
        "0018",
        "0017",
        semear_pendente,
        lambda conn, chave: _contar(
            conn, "SELECT count(*) FROM app_user WHERE id = :chave AND awaiting_approval", chave
        ),
    ),
    Caso(
        "0018",
        "0017",
        semear_bloqueada,
        lambda conn, chave: _contar(
            conn,
            "SELECT count(*) FROM app_user "
            "WHERE id = :chave AND status = 'blocked' AND NOT awaiting_approval",
            chave,
        ),
        rotulo="0018-bloqueada",
    ),
]
IDS = [caso.rotulo or caso.revisao for caso in CASOS]


@pytest.mark.parametrize("caso", CASOS, ids=IDS)
def test_downgrade_com_perda_aborta_e_mantem_a_linha(
    db_settings: Settings, engine: Engine, caso: Caso
) -> None:
    na_revisao(db_settings, caso.revisao)
    with engine.begin() as conn:
        chave = caso.semear(conn)

    with pytest.raises(DBAPIError, match="GS_ALLOW_LOSSY_DOWNGRADE"):
        downgrade_migrations(db_settings, caso.anterior, allow_lossy=False)

    assert current_revision(engine) == caso.revisao
    with engine.connect() as conn:
        assert caso.contar(conn, chave) == 1


@pytest.mark.parametrize("caso", CASOS, ids=IDS)
def test_downgrade_com_licenca_apaga_e_desce(
    db_settings: Settings, engine: Engine, caso: Caso
) -> None:
    na_revisao(db_settings, caso.revisao)
    with engine.begin() as conn:
        caso.semear(conn)

    downgrade_migrations(db_settings, caso.anterior, allow_lossy=True)

    assert current_revision(engine) == caso.anterior


def test_downgrade_sem_linhas_desce_sem_licenca(db_settings: Settings, engine: Engine) -> None:
    na_revisao(db_settings, "0016")
    with engine.begin() as conn:
        # Outros testes da sessão deixam eventos commitados; este quer o banco sem nenhum.
        conn.execute(text("DELETE FROM audit_event WHERE event_type = 'ACCOUNT_LIMITS_CHANGED'"))

    downgrade_migrations(db_settings, "0015", allow_lossy=False)

    assert current_revision(engine) == "0015"


def test_script_offline_do_downgrade_leva_a_guarda(
    db_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    with _alembic_env(db_settings, allow_lossy=False):
        command.downgrade(Config("alembic.ini"), "0016:0015", sql=True)
    sem_licenca = capsys.readouterr().out
    with _alembic_env(db_settings, allow_lossy=True):
        command.downgrade(Config("alembic.ini"), "0016:0015", sql=True)
    com_licenca = capsys.readouterr().out

    guarda = sem_licenca.find("RAISE EXCEPTION")
    assert guarda != -1
    assert "GS_ALLOW_LOSSY_DOWNGRADE=1 aceita a perda" in sem_licenca
    # A guarda vem antes de qualquer perda: aplicado o script, ela aborta antes do DELETE e do DROP.
    assert guarda < sem_licenca.index("DELETE FROM audit_event")
    assert guarda < sem_licenca.index("DROP")
    # A licença vale na geração: o script gerado com ela sai sem a guarda.
    assert "RAISE EXCEPTION" not in com_licenca
    assert "DELETE FROM audit_event" in com_licenca
