"""Regras do banco nas tabelas da fatia 6 (migração 0006; docs/GoblinShiftPlan.md, 3.3 e 3.6).

Mesmo método de `test_schema_constraints.py`: cada teste roda numa transação desfeita no fim, e
cada recusa confere o NOME da constraint, para não passar barrado por outra regra.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, func, insert, inspect, select, text, update
from sqlalchemy.exc import IntegrityError

from goblinshift.capacity import GLOBAL_STORAGE_MAX_BYTES, HolderKind, Resource
from goblinshift.capacity.tables import capacity_pool, resource_reservation
from goblinshift.identity.audit import AuditEventType, AuditOutcome
from goblinshift.identity.tables import app_user, audit_event, login_state, web_session
from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import run_migrations

pytestmark = pytest.mark.integration

AGORA = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
HASH = b"\x02" * 32
HOLDER = UUID("00000000-0000-7000-8000-0000000000aa")
GIB = 1024**3


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    run_migrations(db_settings, "head")
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()


@pytest.fixture
def conn(engine: Engine) -> Iterator[Connection]:
    with engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()


@contextmanager
def barrado_por(conn: Connection, constraint: str) -> Iterator[None]:
    with pytest.raises(IntegrityError, match=constraint), conn.begin_nested():
        yield


def criar_conta(conn: Connection, subject: str = "alice") -> UUID:
    user_id: UUID = conn.execute(
        insert(app_user)
        .values(issuer="admin", subject=subject, display_name=subject)
        .returning(app_user.c.id)
    ).scalar_one()
    return user_id


# --- app_user: termos e exclusão pedida ------------------------------------------------------


def test_conta_antiga_nasce_sem_termos_e_sem_exclusao(conn: Connection) -> None:
    alice = criar_conta(conn)

    row = conn.execute(select(app_user).where(app_user.c.id == alice)).one()

    assert row.terms_version is None
    assert row.terms_accepted_at is None
    assert row.deletion_requested_at is None


@pytest.mark.parametrize(
    ("version", "accepted_at"), [("2026-10", None), (None, AGORA)], ids=["sem-data", "sem-versao"]
)
def test_termos_pela_metade_sao_recusados(
    conn: Connection, version: str | None, accepted_at: datetime | None
) -> None:
    alice = criar_conta(conn)

    with barrado_por(conn, "app_user_terms_check"):
        conn.execute(
            update(app_user)
            .where(app_user.c.id == alice)
            .values(terms_version=version, terms_accepted_at=accepted_at)
        )


def test_termos_com_versao_e_data_sao_aceitos(conn: Connection) -> None:
    alice = criar_conta(conn)

    conn.execute(
        update(app_user)
        .where(app_user.c.id == alice)
        .values(terms_version="2026-10", terms_accepted_at=AGORA)
    )


@pytest.mark.parametrize("version", ["", "v" * 65])
def test_versao_dos_termos_fora_do_tamanho_e_recusada(conn: Connection, version: str) -> None:
    alice = criar_conta(conn)

    with barrado_por(conn, "app_user_terms_version_check"):
        conn.execute(
            update(app_user)
            .where(app_user.c.id == alice)
            .values(terms_version=version, terms_accepted_at=AGORA)
        )


# --- web_session ------------------------------------------------------------------------------


def _sessao(user_id: UUID, **overrides: object) -> dict[str, object]:
    valores: dict[str, object] = {
        "user_id": user_id,
        "secret_hash": HASH,
        "created_at": AGORA,
        "authenticated_at": AGORA,
        "last_seen_at": AGORA,
        "idle_expires_at": AGORA + timedelta(minutes=30),
        "absolute_expires_at": AGORA + timedelta(hours=8),
    }
    return {**valores, **overrides}


def test_sessao_valida_e_aceita(conn: Connection) -> None:
    conn.execute(insert(web_session).values(**_sessao(criar_conta(conn))))


def test_hash_da_sessao_repetido_e_recusado(conn: Connection) -> None:
    alice = criar_conta(conn)
    conn.execute(insert(web_session).values(**_sessao(alice)))

    with barrado_por(conn, "web_session_secret_hash_key"):
        conn.execute(insert(web_session).values(**_sessao(alice)))


def test_hash_da_sessao_de_tamanho_errado_e_recusado(conn: Connection) -> None:
    with barrado_por(conn, "web_session_secret_hash_check"):
        conn.execute(insert(web_session).values(**_sessao(criar_conta(conn), secret_hash=b"x")))


def test_sessao_que_vence_antes_do_login_e_recusada(conn: Connection) -> None:
    with barrado_por(conn, "web_session_absolute_expires_at_check"):
        conn.execute(
            insert(web_session).values(**_sessao(criar_conta(conn), absolute_expires_at=AGORA))
        )


def test_inatividade_alem_do_teto_absoluto_e_recusada(conn: Connection) -> None:
    alice = criar_conta(conn)
    valores = _sessao(alice, idle_expires_at=AGORA + timedelta(hours=8, seconds=1))

    with barrado_por(conn, "web_session_idle_expires_at_check"):
        conn.execute(insert(web_session).values(**valores))


def test_conta_com_sessao_nao_e_apagada(conn: Connection) -> None:
    alice = criar_conta(conn)
    conn.execute(insert(web_session).values(**_sessao(alice)))

    with barrado_por(conn, "web_session_user_id_fkey"):
        conn.execute(app_user.delete().where(app_user.c.id == alice))


# --- login_state ------------------------------------------------------------------------------


def test_estado_de_login_valido_e_aceito(conn: Connection) -> None:
    conn.execute(insert(login_state).values(key="k" * 200, value="v" * 4096, expires_at=AGORA))


@pytest.mark.parametrize(
    ("key", "value", "constraint"),
    [
        ("", "v", "login_state_key_check"),
        ("k" * 201, "v", "login_state_key_check"),
        ("k", "v" * 4097, "login_state_value_check"),
    ],
    ids=["chave-vazia", "chave-longa", "valor-longo"],
)
def test_estado_de_login_fora_dos_limites_e_recusado(
    conn: Connection, key: str, value: str, constraint: str
) -> None:
    with barrado_por(conn, constraint):
        conn.execute(insert(login_state).values(key=key, value=value, expires_at=AGORA))


# --- audit_event ------------------------------------------------------------------------------


def _evento(**overrides: object) -> dict[str, object]:
    valores: dict[str, object] = {
        "user_id": None,
        "event_type": AuditEventType.LOGIN_FAILED.value,
        "outcome": AuditOutcome.FAILURE.value,
        "ip_prefix": None,
        "occurred_at": AGORA,
    }
    return {**valores, **overrides}


@pytest.mark.parametrize("event_type", [event.value for event in AuditEventType])
def test_todo_tipo_de_evento_do_enum_e_aceito_pelo_banco(conn: Connection, event_type: str) -> None:
    conn.execute(insert(audit_event).values(**_evento(event_type=event_type)))


@pytest.mark.parametrize("event_type", ["", "login_succeeded", "OUTRO"])
def test_tipo_de_evento_fora_da_lista_e_recusado(conn: Connection, event_type: str) -> None:
    with barrado_por(conn, "audit_event_event_type_check"):
        conn.execute(insert(audit_event).values(**_evento(event_type=event_type)))


@pytest.mark.parametrize("outcome", [outcome.value for outcome in AuditOutcome])
def test_todo_resultado_do_enum_e_aceito_pelo_banco(conn: Connection, outcome: str) -> None:
    conn.execute(insert(audit_event).values(**_evento(outcome=outcome)))


def test_resultado_fora_da_lista_e_recusado(conn: Connection) -> None:
    with barrado_por(conn, "audit_event_outcome_check"):
        conn.execute(insert(audit_event).values(**_evento(outcome="partial")))


@pytest.mark.parametrize("prefix", ["203.0.113.0/24", "2001:db8:abcd::/48"])
def test_ip_truncado_e_aceito(conn: Connection, prefix: str) -> None:
    conn.execute(insert(audit_event).values(**_evento(ip_prefix=prefix)))


@pytest.mark.parametrize(
    "prefix", ["203.0.113.7/32", "203.0.0.0/16", "2001:db8:abcd:12::/64", "2001:db8::/32"]
)
def test_ip_sem_o_truncamento_exigido_e_recusado(conn: Connection, prefix: str) -> None:
    with barrado_por(conn, "audit_event_ip_prefix_check"):
        conn.execute(insert(audit_event).values(**_evento(ip_prefix=prefix)))


def test_evento_de_conta_inexistente_e_aceito_sem_fk(conn: Connection) -> None:
    # A auditoria sobrevive à purga da conta (ADR 025): o user_id não aponta para app_user.
    conn.execute(insert(audit_event).values(**_evento(user_id=HOLDER)))


def test_conta_com_eventos_pode_ser_apagada(conn: Connection) -> None:
    alice = criar_conta(conn)
    conn.execute(insert(audit_event).values(**_evento(user_id=alice)))

    conn.execute(app_user.delete().where(app_user.c.id == alice))

    restantes = conn.execute(
        select(func.count()).select_from(audit_event).where(audit_event.c.user_id == alice)
    ).scalar_one()
    assert restantes == 1


# --- capacity_pool ----------------------------------------------------------------------------


def test_migracao_cria_o_pool_global_de_armazenamento(conn: Connection) -> None:
    row = conn.execute(select(capacity_pool).where(capacity_pool.c.scope == "global")).one()

    assert row.owner_id is None
    assert row.resource == Resource.STORAGE_BYTES.value
    assert row.max_amount == GLOBAL_STORAGE_MAX_BYTES == 20 * GIB
    assert row.reserved == 0


def _pool_da_conta(conn: Connection, owner_id: UUID | None, **overrides: object) -> None:
    valores: dict[str, object] = {
        "scope": "account",
        "owner_id": owner_id,
        "resource": Resource.STORAGE_BYTES.value,
        "max_amount": 4 * GIB,
    }
    conn.execute(insert(capacity_pool).values(**{**valores, **overrides}))


def test_pool_da_conta_valido_e_aceito(conn: Connection) -> None:
    _pool_da_conta(conn, criar_conta(conn))


def test_segundo_pool_da_mesma_conta_e_recurso_e_recusado(conn: Connection) -> None:
    alice = criar_conta(conn)
    _pool_da_conta(conn, alice)

    with barrado_por(conn, "capacity_pool_scope_owner_id_resource_key"):
        _pool_da_conta(conn, alice)


def test_segundo_pool_global_do_mesmo_recurso_e_recusado(conn: Connection) -> None:
    # NULLS NOT DISTINCT: sem isso, dois NULL em owner_id não colidiriam.
    with barrado_por(conn, "capacity_pool_scope_owner_id_resource_key"):
        _pool_da_conta(conn, None, scope="global", max_amount=GIB)


@pytest.mark.parametrize(
    ("scope", "with_owner"),
    [("account", False), ("global", True)],
    ids=["conta-sem-dono", "global-com-dono"],
)
def test_escopo_incoerente_com_o_dono_e_recusado(
    conn: Connection, scope: str, with_owner: bool
) -> None:
    owner = criar_conta(conn) if with_owner else None

    with barrado_por(conn, "capacity_pool_owner_check"):
        _pool_da_conta(conn, owner, scope=scope)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"scope": "tenant"}, "capacity_pool_scope_check"),
        ({"resource": "cpu_seconds"}, "capacity_pool_resource_check"),
        ({"reserved": -1}, "capacity_pool_reserved_check"),
        ({"reserved": 4 * GIB + 1}, "capacity_pool_reserved_check"),
    ],
    ids=["escopo", "recurso", "reserva-negativa", "reserva-acima-do-teto"],
)
def test_pool_fora_das_regras_e_recusado(
    conn: Connection, overrides: dict[str, object], constraint: str
) -> None:
    with barrado_por(conn, constraint):
        _pool_da_conta(conn, criar_conta(conn), **overrides)


def test_reserva_igual_ao_teto_e_aceita(conn: Connection) -> None:
    _pool_da_conta(conn, criar_conta(conn), reserved=4 * GIB)


# --- resource_reservation ---------------------------------------------------------------------


def _reservar(conn: Connection, owner_id: UUID, **overrides: object) -> None:
    valores: dict[str, object] = {
        "owner_id": owner_id,
        "holder_kind": HolderKind.UPLOAD.value,
        "holder_id": HOLDER,
        "resource": Resource.STORAGE_BYTES.value,
        "amount": 10,
    }
    conn.execute(insert(resource_reservation).values(**{**valores, **overrides}))


@pytest.mark.parametrize("holder_kind", [kind.value for kind in HolderKind])
def test_todo_tipo_de_detentor_do_enum_e_aceito(conn: Connection, holder_kind: str) -> None:
    _reservar(conn, criar_conta(conn), holder_kind=holder_kind)


def test_reserva_de_zero_e_aceita(conn: Connection) -> None:
    _reservar(conn, criar_conta(conn), amount=0)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"holder_kind": "download"}, "resource_reservation_holder_kind_check"),
        ({"resource": "cpu_seconds"}, "resource_reservation_resource_check"),
        ({"amount": -1}, "resource_reservation_amount_check"),
    ],
    ids=["detentor", "recurso", "quantidade-negativa"],
)
def test_reserva_fora_das_regras_e_recusada(
    conn: Connection, overrides: dict[str, object], constraint: str
) -> None:
    with barrado_por(conn, constraint):
        _reservar(conn, criar_conta(conn), **overrides)


def test_segunda_reserva_viva_do_mesmo_detentor_e_recusada(conn: Connection) -> None:
    alice = criar_conta(conn)
    _reservar(conn, alice)

    with barrado_por(conn, "resource_reservation_live_holder_idx"):
        _reservar(conn, alice)


def test_nova_reserva_do_detentor_depois_de_liberar_a_anterior(conn: Connection) -> None:
    alice = criar_conta(conn)
    _reservar(conn, alice)
    conn.execute(update(resource_reservation).values(released_at=AGORA))

    _reservar(conn, alice)


# --- Migração reversível ----------------------------------------------------------------------


def test_downgrade_da_0006_volta_ao_schema_da_0005(db_settings: Settings, engine: Engine) -> None:
    novas = {"web_session", "login_state", "audit_event", "capacity_pool", "resource_reservation"}
    # Do head até a base passa pelo downgrade da 0006; depois sobe só até a 0005.
    run_migrations(db_settings, "base")
    run_migrations(db_settings, "0005")
    with engine.connect() as connection:
        tabelas = set(inspect(connection).get_table_names())
        colunas = {c["name"] for c in inspect(connection).get_columns("app_user")}
    assert tabelas.isdisjoint(novas)
    assert colunas.isdisjoint({"terms_version", "terms_accepted_at", "deletion_requested_at"})

    run_migrations(db_settings, "head")
    with engine.connect() as connection:
        assert set(inspect(connection).get_table_names()) >= novas
        pools = connection.execute(text("SELECT count(*) FROM capacity_pool")).scalar_one()
    assert pools == 1
