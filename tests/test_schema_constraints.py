"""O banco, e não o código, impõe as regras do modelo (docs/GoblinShiftPlan.md, 3.3 e 3.4).

Cada teste roda numa transação que sofre rollback no fim, então os dados não vazam entre eles.
Uma violação aborta a transação no PostgreSQL; por isso cada tentativa que deve falhar roda
dentro de um SAVEPOINT (`barrado_por`), e o teste pode continuar na mesma transação para provar
que a mesma operação passa quando a condição muda.

Cada falha confere o NOME da constraint na mensagem: sem isso, um teste passaria barrado por
outra regra (por exemplo, um CHECK diferente do que o teste pretende exercitar).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, Engine, Table, insert, inspect, update
from sqlalchemy.exc import IntegrityError

# Importar os módulos registra as tabelas no MetaData comum.
import goblinshift.capacity.tables  # noqa: F401
from goblinshift.files.states import ArtifactRole
from goblinshift.files.tables import artifact, object_use, stored_object, upload
from goblinshift.identity.tables import api_key, app_user
from goblinshift.platform.db import make_engine, metadata
from goblinshift.platform.settings import Settings
from goblinshift.runs.tables import attempt, idempotency_record, run, run_input, run_output
from tests.conftest import run_migrations

pytestmark = pytest.mark.integration

AGORA = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
UMA_HORA_DEPOIS = AGORA + timedelta(hours=1)
# `created_at` da chave usa o relógio do banco; o vencimento precisa estar além de qualquer "agora".
MUITO_DEPOIS = datetime(2100, 1, 1, tzinfo=UTC)
SHA256 = b"\x01" * 32
CHAVE_VALIDA = "a" * 16
DETENTOR_RUN = UUID("00000000-0000-7000-8000-000000000001")
OUTRO_DETENTOR_RUN = UUID("00000000-0000-7000-8000-000000000002")

# Tabelas que a migração 0002 cria, sem a `alembic_version` do próprio Alembic.
TABELAS_DO_MODELO = frozenset(metadata.tables)


# --- Fixtures ---------------------------------------------------------------------------------


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
    """Espera que o banco recuse a operação por `constraint`, sem abortar a transação."""
    with pytest.raises(IntegrityError, match=constraint), conn.begin_nested():
        yield


# --- Test data builders -----------------------------------------------------------------------


def _insert_returning_id(conn: Connection, table: Table, **values: object) -> UUID:
    new_id: UUID = conn.execute(insert(table).values(**values).returning(table.c.id)).scalar_one()
    return new_id


def criar_conta(conn: Connection, subject: str = "alice") -> UUID:
    return _insert_returning_id(
        conn, app_user, issuer="admin", subject=subject, display_name=subject
    )


def criar_objeto_unico(conn: Connection, owner_id: UUID, storage_key: str) -> UUID:
    return _insert_returning_id(conn, stored_object, owner_id=owner_id, storage_key=storage_key)


def inserir_upload(conn: Connection, owner_id: UUID, object_id: UUID) -> UUID:
    return _insert_returning_id(
        conn,
        upload,
        owner_id=owner_id,
        object_id=object_id,
        original_name="dados.csv",
        expected_size=10,
        expires_at=UMA_HORA_DEPOIS,
    )


def criar_upload(conn: Connection, owner_id: UUID, storage_key: str) -> UUID:
    return inserir_upload(conn, owner_id, criar_objeto_unico(conn, owner_id, storage_key))


def criar_run(
    conn: Connection,
    owner_id: UUID,
    *,
    status: str = "queued",
    phase: str | None = None,
    **overrides: object,
) -> UUID:
    valores: dict[str, object] = {
        "owner_id": owner_id,
        "recipe_id": "csv-janitor",
        "recipe_version": 1,
        "executor_digest": "sha256:" + "0" * 64,
        "parameters": {},
        "limits": {},
        "status": status,
        "phase": phase,
        "queue_expires_at": UMA_HORA_DEPOIS,
    }
    return _insert_returning_id(conn, run, **{**valores, **overrides})


def criar_run_terminal(conn: Connection, owner_id: UUID) -> UUID:
    return criar_run(conn, owner_id, status="failed", error_code="INFRA_POISON", finished_at=AGORA)


def criar_tentativa(conn: Connection, run_id: UUID, numero: int, **overrides: object) -> UUID:
    valores: dict[str, object] = {
        "run_id": run_id,
        "number": numero,
        "generation": numero,
        "container_name": f"gs-exec-{run_id}-{numero}",
        "lease_expires_at": UMA_HORA_DEPOIS,
    }
    return _insert_returning_id(conn, attempt, **{**valores, **overrides})


def registrar_idempotencia(conn: Connection, owner_id: UUID, run_id: UUID, idem_key: str) -> None:
    conn.execute(
        insert(idempotency_record).values(
            owner_id=owner_id,
            operation="POST /runs",
            idem_key=idem_key,
            command_hash=SHA256,
            run_id=run_id,
            expires_at=UMA_HORA_DEPOIS,
        )
    )


def abrir_uso(conn: Connection, object_id: UUID, holder_id: UUID, holder_kind: str = "run") -> UUID:
    return _insert_returning_id(
        conn, object_use, object_id=object_id, holder_kind=holder_kind, holder_id=holder_id
    )


# --- 1. Migrações batem com os modelos --------------------------------------------------------


def test_migracoes_batem_com_os_modelos(engine: Engine) -> None:
    with engine.connect() as connection:
        contexto = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        diferencas = compare_metadata(contexto, metadata)

    assert diferencas == []


def test_migracao_e_reversivel_de_base_a_head(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "base")
    with engine.connect() as connection:
        tabelas_em_base = set(inspect(connection).get_table_names())
    assert tabelas_em_base.isdisjoint(TABELAS_DO_MODELO)

    run_migrations(db_settings, "head")
    with engine.connect() as connection:
        tabelas_em_head = set(inspect(connection).get_table_names())
    assert tabelas_em_head >= TABELAS_DO_MODELO


# --- 2. FK composta com proprietário ----------------------------------------------------------


def test_upload_aponta_para_objeto_da_mesma_conta(conn: Connection) -> None:
    alice = criar_conta(conn, "alice")
    objeto = criar_objeto_unico(conn, alice, "uploads/alice/1")

    assert inserir_upload(conn, alice, objeto) is not None


def test_upload_nao_aponta_para_objeto_de_outra_conta(conn: Connection) -> None:
    alice = criar_conta(conn, "alice")
    bob = criar_conta(conn, "bob")
    objeto_de_bob = criar_objeto_unico(conn, bob, "uploads/bob/1")

    with barrado_por(conn, "upload_owner_id_object_id_fkey"):
        inserir_upload(conn, alice, objeto_de_bob)


def test_artifact_nao_aponta_para_objeto_de_outra_conta(conn: Connection) -> None:
    alice = criar_conta(conn, "alice")
    bob = criar_conta(conn, "bob")
    objeto_de_bob = criar_objeto_unico(conn, bob, "outputs/bob/1")

    with barrado_por(conn, "artifact_owner_id_object_id_fkey"):
        conn.execute(
            insert(artifact).values(
                owner_id=alice,
                object_id=objeto_de_bob,
                role="output",
                file_name="saida.csv",
                expires_at=UMA_HORA_DEPOIS,
            )
        )


def test_run_input_liga_run_e_upload_da_mesma_conta(conn: Connection) -> None:
    alice = criar_conta(conn, "alice")
    run_de_alice = criar_run(conn, alice)
    upload_de_alice = criar_upload(conn, alice, "uploads/alice/1")

    conn.execute(
        insert(run_input).values(
            run_id=run_de_alice, position=0, owner_id=alice, upload_id=upload_de_alice
        )
    )


def test_run_input_nao_liga_run_de_uma_conta_a_upload_de_outra(conn: Connection) -> None:
    alice = criar_conta(conn, "alice")
    bob = criar_conta(conn, "bob")
    run_de_alice = criar_run(conn, alice)
    upload_de_bob = criar_upload(conn, bob, "uploads/bob/1")

    with barrado_por(conn, "run_input_owner_id_upload_id_fkey"):
        conn.execute(
            insert(run_input).values(
                run_id=run_de_alice, position=0, owner_id=alice, upload_id=upload_de_bob
            )
        )


def test_run_input_nao_declara_como_dono_a_conta_que_nao_e_da_run(conn: Connection) -> None:
    # Quem escreve `owner_id = bob` para fugir da FK do upload cai na FK da run.
    alice = criar_conta(conn, "alice")
    bob = criar_conta(conn, "bob")
    run_de_alice = criar_run(conn, alice)
    upload_de_bob = criar_upload(conn, bob, "uploads/bob/1")

    with barrado_por(conn, "run_input_owner_id_run_id_fkey"):
        conn.execute(
            insert(run_input).values(
                run_id=run_de_alice, position=0, owner_id=bob, upload_id=upload_de_bob
            )
        )


def test_run_output_nao_liga_run_a_artefato_de_outra_conta(conn: Connection) -> None:
    alice = criar_conta(conn, "alice")
    bob = criar_conta(conn, "bob")
    run_de_alice = criar_run(conn, alice)
    objeto_de_bob = criar_objeto_unico(conn, bob, "outputs/bob/1")
    artefato_de_bob = _insert_returning_id(
        conn,
        artifact,
        owner_id=bob,
        object_id=objeto_de_bob,
        role="output",
        file_name="saida.csv",
        expires_at=UMA_HORA_DEPOIS,
    )

    with barrado_por(conn, "run_output_owner_id_artifact_id_fkey"):
        conn.execute(
            insert(run_output).values(
                run_id=run_de_alice,
                position=0,
                owner_id=alice,
                artifact_id=artefato_de_bob,
                logical_name="saida.csv",
            )
        )


# --- 3. Uma execução não terminal por conta ---------------------------------------------------


@pytest.mark.parametrize(
    ("status", "phase"),
    [
        pytest.param("queued", None, id="queued"),
        pytest.param("running", "processing", id="running"),
        pytest.param("stopping", None, id="stopping"),
    ],
)
def test_segunda_run_nao_terminal_da_mesma_conta_e_recusada(
    conn: Connection, status: str, phase: str | None
) -> None:
    alice = criar_conta(conn)
    criar_run(conn, alice, status=status, phase=phase)

    with barrado_por(conn, "run_one_active_per_owner_idx"):
        criar_run(conn, alice)


def test_contas_diferentes_tem_cada_uma_sua_run_ativa(conn: Connection) -> None:
    alice = criar_conta(conn, "alice")
    bob = criar_conta(conn, "bob")
    criar_run(conn, alice)

    criar_run(conn, bob)


def test_nova_run_entra_quando_a_anterior_virou_terminal(conn: Connection) -> None:
    alice = criar_conta(conn)
    primeira = criar_run(conn, alice)
    with barrado_por(conn, "run_one_active_per_owner_idx"):
        criar_run(conn, alice)

    conn.execute(
        update(run)
        .where(run.c.id == primeira)
        .values(status="failed", error_code="QUEUE_EXPIRED", finished_at=AGORA)
    )

    criar_run(conn, alice)


def test_varias_runs_terminais_da_mesma_conta_convivem(conn: Connection) -> None:
    alice = criar_conta(conn)
    criar_run_terminal(conn, alice)

    criar_run_terminal(conn, alice)


# --- 4. Uma tentativa viva por run ------------------------------------------------------------


def test_segunda_tentativa_viva_na_mesma_run_e_recusada(conn: Connection) -> None:
    alice = criar_conta(conn)
    run_id = criar_run(conn, alice)
    criar_tentativa(conn, run_id, 1)

    with barrado_por(conn, "attempt_one_alive_per_run_idx"):
        criar_tentativa(conn, run_id, 2)


def test_nova_tentativa_entra_depois_de_encerrar_a_anterior(conn: Connection) -> None:
    alice = criar_conta(conn)
    run_id = criar_run(conn, alice)
    primeira = criar_tentativa(conn, run_id, 1)
    conn.execute(update(attempt).where(attempt.c.id == primeira).values(ended_at=AGORA))

    criar_tentativa(conn, run_id, 2)


def test_quinto_claim_e_recusado(conn: Connection) -> None:
    # Cada claim cria uma tentativa; o quinto vira INFRA_POISON, nunca uma linha nova.
    alice = criar_conta(conn)
    run_id = criar_run(conn, alice)
    for number in range(1, 5):
        criar_tentativa(conn, run_id, number, ended_at=AGORA)

    with barrado_por(conn, "attempt_number_check"):
        criar_tentativa(conn, run_id, 5)


# --- 5. CHECKs --------------------------------------------------------------------------------


def test_phase_sem_status_running_e_recusada(conn: Connection) -> None:
    alice = criar_conta(conn)

    with barrado_por(conn, "run_phase_only_running_check"):
        criar_run(conn, alice, status="queued", phase="inspecting")


def test_status_running_sem_phase_e_recusado(conn: Connection) -> None:
    alice = criar_conta(conn)

    with barrado_por(conn, "run_phase_only_running_check"):
        criar_run(conn, alice, status="running", phase=None)


def test_status_running_com_phase_e_aceito(conn: Connection) -> None:
    alice = criar_conta(conn)

    criar_run(conn, alice, status="running", phase="inspecting")


def test_failed_sem_error_code_e_recusado(conn: Connection) -> None:
    alice = criar_conta(conn)

    with barrado_por(conn, "run_failed_has_code_check"):
        criar_run(conn, alice, status="failed", finished_at=AGORA)


@pytest.mark.parametrize(
    "status",
    ["succeeded", "succeeded_with_warnings", "canceled", "failed"],
)
def test_status_terminal_sem_finished_at_e_recusado(conn: Connection, status: str) -> None:
    alice = criar_conta(conn)

    with barrado_por(conn, "run_finished_at_terminal_check"):
        criar_run(conn, alice, status=status, error_code="INFRA_POISON")


def test_finished_at_em_run_nao_terminal_e_recusado(conn: Connection) -> None:
    alice = criar_conta(conn)

    with barrado_por(conn, "run_finished_at_terminal_check"):
        criar_run(conn, alice, status="queued", finished_at=AGORA)


@pytest.mark.parametrize(
    "idem_key",
    [
        pytest.param("a" * 15, id="15-caracteres"),
        pytest.param("a" * 65, id="65-caracteres"),
        pytest.param("a" * 15 + " ", id="espaco"),
        pytest.param("a" * 15 + "/", id="barra"),
        pytest.param("a" * 15 + "á", id="acento"),
    ],
)
def test_idem_key_fora_do_formato_e_recusada(conn: Connection, idem_key: str) -> None:
    alice = criar_conta(conn)
    run_id = criar_run(conn, alice)

    with barrado_por(conn, "idempotency_record_idem_key_check"):
        registrar_idempotencia(conn, alice, run_id, idem_key)


@pytest.mark.parametrize(
    "idem_key",
    [
        pytest.param("a" * 16, id="16-caracteres"),
        pytest.param("A1_-" * 16, id="64-caracteres-do-alfabeto"),
    ],
)
def test_idem_key_no_formato_e_aceita(conn: Connection, idem_key: str) -> None:
    alice = criar_conta(conn)
    run_id = criar_run(conn, alice)

    registrar_idempotencia(conn, alice, run_id, idem_key)


def test_idem_key_repetida_na_mesma_conta_e_operacao_e_recusada(conn: Connection) -> None:
    alice = criar_conta(conn)
    run_id = criar_run(conn, alice)
    registrar_idempotencia(conn, alice, run_id, CHAVE_VALIDA)

    with barrado_por(conn, "idempotency_record_pkey"):
        registrar_idempotencia(conn, alice, run_id, CHAVE_VALIDA)


def test_idem_key_igual_em_outra_conta_e_independente(conn: Connection) -> None:
    alice = criar_conta(conn, "alice")
    bob = criar_conta(conn, "bob")
    registrar_idempotencia(conn, alice, criar_run(conn, alice), CHAVE_VALIDA)

    registrar_idempotencia(conn, bob, criar_run(conn, bob), CHAVE_VALIDA)


@pytest.mark.parametrize(
    ("sha256", "size_bytes"),
    [
        pytest.param(None, 10, id="sem-sha256"),
        pytest.param(SHA256, None, id="sem-size_bytes"),
        pytest.param(None, None, id="sem-nenhum"),
    ],
)
def test_objeto_committed_sem_conteudo_conhecido_e_recusado(
    conn: Connection, sha256: bytes | None, size_bytes: int | None
) -> None:
    alice = criar_conta(conn)

    with barrado_por(conn, "stored_object_content_known_check"):
        _insert_returning_id(
            conn,
            stored_object,
            owner_id=alice,
            storage_key="uploads/alice/1",
            state="committed",
            sha256=sha256,
            size_bytes=size_bytes,
        )


def test_objeto_committed_com_conteudo_conhecido_e_aceito(conn: Connection) -> None:
    alice = criar_conta(conn)

    _insert_returning_id(
        conn,
        stored_object,
        owner_id=alice,
        storage_key="uploads/alice/1",
        state="committed",
        sha256=SHA256,
        size_bytes=10,
    )


def test_objeto_pending_ainda_nao_precisa_conhecer_o_conteudo(conn: Connection) -> None:
    alice = criar_conta(conn)

    criar_objeto_unico(conn, alice, "uploads/alice/1")


def test_sha256_de_tamanho_errado_e_recusado(conn: Connection) -> None:
    alice = criar_conta(conn)

    with barrado_por(conn, "stored_object_sha256_check"):
        _insert_returning_id(
            conn,
            stored_object,
            owner_id=alice,
            storage_key="uploads/alice/1",
            state="committed",
            sha256=b"\x01" * 31,
            size_bytes=10,
        )


def test_chave_de_api_sem_escopo_e_recusada(conn: Connection) -> None:
    alice = criar_conta(conn)

    with barrado_por(conn, "api_key_scopes_check"):
        conn.execute(
            insert(api_key).values(
                owner_id=alice,
                public_id="pub-1",
                secret_hash=SHA256,
                scopes=[],
                expires_at=MUITO_DEPOIS,
            )
        )


def test_chave_de_api_que_vence_antes_de_nascer_e_recusada(conn: Connection) -> None:
    alice = criar_conta(conn)

    with barrado_por(conn, "api_key_expires_at_check"):
        conn.execute(
            insert(api_key).values(
                owner_id=alice,
                public_id="pub-1",
                secret_hash=SHA256,
                scopes=["runs:write"],
                created_at=AGORA,
                expires_at=AGORA,
            )
        )


def _inserir_artefato(conn: Connection, role: str) -> None:
    alice = criar_conta(conn, "alice")
    objeto = criar_objeto_unico(conn, alice, "outputs/alice/1")
    conn.execute(
        insert(artifact).values(
            owner_id=alice,
            object_id=objeto,
            role=role,
            file_name="images/foto.jpg",
            expires_at=UMA_HORA_DEPOIS,
        )
    )


@pytest.mark.parametrize("role", [role.value for role in ArtifactRole])
def test_todo_papel_do_enum_e_aceito_pelo_banco(conn: Connection, role: str) -> None:
    # Fatia 5 (migração 0005): manifest e package entram ao lado de output e report.
    _inserir_artefato(conn, role)


@pytest.mark.parametrize("role", ["preview", "outro", "MANIFEST", ""])
def test_papel_fora_da_lista_e_recusado(conn: Connection, role: str) -> None:
    with barrado_por(conn, "artifact_role_check"):
        _inserir_artefato(conn, role)


def test_papeis_da_fatia_5_estao_no_enum() -> None:
    assert [role.value for role in ArtifactRole] == ["output", "report", "manifest", "package"]


# --- 6. Uso de objeto -------------------------------------------------------------------------


def test_mesmo_detentor_nao_abre_dois_usos_simultaneos_do_objeto(conn: Connection) -> None:
    alice = criar_conta(conn)
    objeto = criar_objeto_unico(conn, alice, "uploads/alice/1")
    abrir_uso(conn, objeto, DETENTOR_RUN)

    with barrado_por(conn, "object_use_open_idx"):
        abrir_uso(conn, objeto, DETENTOR_RUN)


def test_detentores_diferentes_usam_o_mesmo_objeto_ao_mesmo_tempo(conn: Connection) -> None:
    alice = criar_conta(conn)
    objeto = criar_objeto_unico(conn, alice, "uploads/alice/1")
    abrir_uso(conn, objeto, DETENTOR_RUN)

    abrir_uso(conn, objeto, OUTRO_DETENTOR_RUN)
    abrir_uso(conn, objeto, DETENTOR_RUN, holder_kind="download")


def test_detentor_abre_novo_uso_depois_de_encerrar_o_anterior(conn: Connection) -> None:
    alice = criar_conta(conn)
    objeto = criar_objeto_unico(conn, alice, "uploads/alice/1")
    primeiro = abrir_uso(conn, objeto, DETENTOR_RUN)
    conn.execute(update(object_use).where(object_use.c.id == primeiro).values(ended_at=AGORA))

    abrir_uso(conn, objeto, DETENTOR_RUN)


# --- 7. uuidv7() como default -----------------------------------------------------------------


def test_id_nasce_como_uuid_versao_7_e_cresce_no_tempo(conn: Connection) -> None:
    primeiro = criar_conta(conn, "alice")
    segundo = criar_conta(conn, "bob")

    assert (primeiro.version, segundo.version) == (7, 7)
    assert primeiro < segundo
