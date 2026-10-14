"""Purga das contas em exclusão e retenção da identidade na manutenção (1.8; Frente C).

A manutenção roda os dois ciclos, o de execuções e arquivos (`Maintenance`) e o de contas
(`AccountJobs`), como `serve` faz; a remoção física dos arquivos é a da retenção normal.
"""

import hashlib
import logging
import secrets
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Table, func, insert, select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.maintenance import AccountJobs, AccountJobsConfig, serve
from goblinshift.files import purge as files_purge
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import artifact, object_use, stored_object, transfer_slot, upload
from goblinshift.identity import IssuedKey, Scope, issue_key, request_account_deletion
from goblinshift.identity.tables import api_key, app_user, audit_event, login_state, web_session
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.settings import Settings
from goblinshift.runs.lifecycle import Status
from goblinshift.runs.maintenance import Maintenance
from goblinshift.runs.purge import cancel_active_run
from goblinshift.runs.tables import attempt, idempotency_record, run, run_input, run_output
from tests.files.support import create_intent
from tests.identity.pending_fronts import install_domain_stand_ins
from tests.runs.worker_support import claim, coordinator, create_user, local_runner, queue_run

pytestmark = pytest.mark.integration

MAX_CYCLES = 10

OWNED_TABLES: dict[str, tuple[Table, str]] = {
    "app_user": (app_user, "id"),
    "api_key": (api_key, "owner_id"),
    "web_session": (web_session, "user_id"),
    "run": (run, "owner_id"),
    "run_input": (run_input, "owner_id"),
    "run_output": (run_output, "owner_id"),
    "idempotency_record": (idempotency_record, "owner_id"),
    "upload": (upload, "owner_id"),
    "artifact": (artifact, "owner_id"),
    "stored_object": (stored_object, "owner_id"),
    "transfer_slot": (transfer_slot, "owner_id"),
}


@pytest.fixture(autouse=True)
def stand_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    install_domain_stand_ins(monkeypatch)


def count_owned(factory: sessionmaker[Session], owner_id: uuid.UUID) -> dict[str, int]:
    with factory() as session:
        return {
            name: session.execute(
                select(func.count()).select_from(table).where(table.c[column] == owner_id)
            ).scalar_one()
            for name, (table, column) in OWNED_TABLES.items()
        }


def full_account(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path, subject: str
) -> tuple[uuid.UUID, IssuedKey]:
    """Conta com execução terminada (entradas, saídas, tentativa), upload incompleto, chave e
    sessão: tudo o que um uso real deixa no banco e no disco."""
    owner = create_user(factory, subject)
    queue_run(factory, storage, owner)
    coordinator(factory, storage, local_runner(work_root)).run(claim(factory, storage))
    create_intent(factory, owner)
    now = datetime.now(UTC)
    with unit_of_work(factory) as session:
        key = issue_key(session, owner_id=owner, scopes=frozenset(Scope), now=now)
        session.execute(
            insert(web_session).values(
                user_id=owner,
                secret_hash=hashlib.sha256(secrets.token_bytes(32)).digest(),
                authenticated_at=now,
                last_seen_at=now,
                idle_expires_at=now + timedelta(minutes=30),
                absolute_expires_at=now + timedelta(hours=8),
            )
        )
    return owner, key


def committed_keys(factory: sessionmaker[Session], owner_id: uuid.UUID) -> list[str]:
    """Chaves dos objetos com conteúdo no disco (o upload incompleto ainda não tem arquivo)."""
    with factory() as session:
        return list(
            session.execute(
                select(stored_object.c.storage_key).where(
                    stored_object.c.owner_id == owner_id, stored_object.c.state == "committed"
                )
            ).scalars()
        )


def on_disk(storage: FileStorage, key: str) -> bool:
    return storage.root.joinpath(*key.split("/")).exists()


def request_deletion(factory: sessionmaker[Session], owner_id: uuid.UUID) -> None:
    with unit_of_work(factory) as session:
        request_account_deletion(session, owner_id, datetime.now(UTC))


def cycles_until_purged(
    factory: sessionmaker[Session],
    maintenance: Maintenance,
    jobs: AccountJobs,
    owner_id: uuid.UUID,
) -> int:
    for cycle in range(1, MAX_CYCLES + 1):
        maintenance.run_cycle()
        jobs.run_cycle()
        if count_owned(factory, owner_id)["app_user"] == 0:
            return cycle
    raise AssertionError("a conta não foi purgada")


@pytest.fixture
def maintenance(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> Maintenance:
    return Maintenance(factory, storage, local_runner(work_root))


def test_delete_me_remove_tudo_em_poucos_ciclos_sem_usos(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    maintenance: Maintenance,
) -> None:
    owner, _ = full_account(factory, storage, work_root, "ana")
    keys = committed_keys(factory, owner)
    assert keys
    assert all(on_disk(storage, key) for key in keys)
    with factory() as session:
        run_ids = list(session.execute(select(run.c.id).where(run.c.owner_id == owner)).scalars())
    request_deletion(factory, owner)

    cycles = cycles_until_purged(factory, maintenance, AccountJobs(factory), owner)

    assert cycles <= 3
    assert set(count_owned(factory, owner).values()) == {0}
    assert not any(on_disk(storage, key) for key in keys)
    with factory() as session:
        attempts = session.execute(
            select(func.count()).select_from(attempt).where(attempt.c.run_id.in_(run_ids))
        ).scalar_one()
        uses = session.execute(select(func.count()).select_from(object_use)).scalar_one()
    assert attempts == 0
    assert uses == 0


def test_delete_me_outra_conta_intacta(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    maintenance: Maintenance,
) -> None:
    owner, _ = full_account(factory, storage, work_root, "ana")
    other, other_key = full_account(factory, storage, work_root, "bia")
    before = count_owned(factory, other)
    other_files = committed_keys(factory, other)
    request_deletion(factory, owner)

    cycles_until_purged(factory, maintenance, AccountJobs(factory), owner)

    assert count_owned(factory, other) == before
    assert all(on_disk(storage, key) for key in other_files)
    with factory() as session:
        revoked = session.execute(
            select(api_key.c.revoked_at).where(api_key.c.public_id == other_key.public_id)
        ).scalar_one()
    assert revoked is None


def test_purga_interrompida_no_meio_e_retomada_termina(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    maintenance: Maintenance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, _ = full_account(factory, storage, work_root, "ana")
    request_deletion(factory, owner)
    jobs = AccountJobs(factory, config=AccountJobsConfig(batch_size=1))
    real_batch = files_purge.purge_owner_batch
    calls = 0

    def crash_on_second_batch(session: Session, owner_id: uuid.UUID, limit: int) -> bool:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ConnectionError("o processo caiu no meio da purga")
        return real_batch(session, owner_id, limit)

    monkeypatch.setattr(files_purge, "purge_owner_batch", crash_on_second_batch)
    maintenance.run_cycle()
    jobs.run_cycle()
    maintenance.run_cycle()
    with pytest.raises(ConnectionError):
        jobs.run_cycle()
    assert count_owned(factory, owner)["app_user"] == 1

    monkeypatch.setattr(files_purge, "purge_owner_batch", real_batch)
    cycles_until_purged(factory, maintenance, jobs, owner)

    assert set(count_owned(factory, owner).values()) == {0}


def test_execucao_ativa_adia_a_purga_ate_terminar(
    factory: sessionmaker[Session], storage: FileStorage, maintenance: Maintenance
) -> None:
    owner = create_user(factory, "ana")
    queue_run(factory, storage, owner)
    request_deletion(factory, owner)
    jobs = AccountJobs(factory)

    maintenance.run_cycle()
    counts = jobs.run_cycle()

    assert counts["accounts_purged"] == 0
    assert count_owned(factory, owner)["run"] == 1

    result = cancel_active_run(factory, storage, owner)
    assert result is not None
    assert result.status == Status.CANCELED
    cycles_until_purged(factory, maintenance, jobs, owner)


def test_conta_sem_pedido_nao_e_purgada(
    factory: sessionmaker[Session], storage: FileStorage, maintenance: Maintenance
) -> None:
    owner = create_user(factory, "ana")
    queue_run(factory, storage, owner)

    maintenance.run_cycle()
    AccountJobs(factory).run_cycle()

    assert count_owned(factory, owner)["app_user"] == 1
    assert count_owned(factory, owner)["run"] == 1


def test_auditoria_da_conta_purgada_fica(
    factory: sessionmaker[Session], storage: FileStorage, maintenance: Maintenance
) -> None:
    owner = create_user(factory, "ana")
    with unit_of_work(factory) as session:
        session.execute(
            insert(audit_event).values(
                user_id=owner, event_type="ACCOUNT_DELETION_REQUESTED", outcome="success"
            )
        )
    request_deletion(factory, owner)

    cycles_until_purged(factory, maintenance, AccountJobs(factory), owner)

    with factory() as session:
        users = session.execute(select(audit_event.c.user_id)).scalars().all()
    assert users == [owner]


# --- Retenção e alerta ------------------------------------------------------------------------


def test_ciclo_aplica_a_retencao_da_identidade(factory: sessionmaker[Session]) -> None:
    now = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    owner = create_user(factory, "ana")
    with unit_of_work(factory) as session:
        session.execute(
            insert(login_state).values(key="k", value="{}", expires_at=now - timedelta(minutes=1))
        )
        session.execute(
            insert(web_session).values(
                user_id=owner,
                secret_hash=hashlib.sha256(b"s").digest(),
                authenticated_at=now - timedelta(days=9),
                last_seen_at=now - timedelta(days=9),
                idle_expires_at=now - timedelta(days=9) + timedelta(minutes=30),
                absolute_expires_at=now - timedelta(days=9) + timedelta(hours=8),
            )
        )
        session.execute(
            insert(audit_event).values(
                user_id=owner,
                event_type="LOGOUT",
                outcome="success",
                occurred_at=now - timedelta(days=91),
            )
        )

    counts = AccountJobs(factory, clock=lambda: now).run_cycle()

    assert counts["login_states_removed"] == 1
    assert counts["web_sessions_removed"] == 1
    assert counts["audit_events_removed"] == 1


def test_exclusao_atrasada_gera_alerta(
    factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    now = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    owner = create_user(factory, "ana")
    with unit_of_work(factory) as session:
        request_account_deletion(session, owner, now - timedelta(hours=21))
        # A conta tem execução ativa: a purga não termina e a exclusão fica atrasada.
        session.execute(
            insert(run).values(
                owner_id=owner,
                recipe_id="csv-janitor",
                recipe_version=1,
                executor_digest="local-dev",
                parameters={},
                limits={},
                queue_expires_at=now + timedelta(hours=1),
            )
        )

    with caplog.at_level(logging.WARNING, logger="goblinshift.maintenance"):
        counts = AccountJobs(factory, clock=lambda: now).run_cycle()

    assert counts["accounts_deletion_overdue"] == 1
    alerts = [r for r in caplog.records if getattr(r, "fields", {}).get("alert")]
    assert [r.fields["alert"] for r in alerts] == ["account_deletion_overdue"]  # type: ignore[attr-defined]


def test_serve_roda_o_ciclo_de_contas_mesmo_se_o_de_execucoes_falhar(
    db_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutdown = threading.Event()
    account_cycles: list[bool] = []

    def failing_cycle(_self: Maintenance) -> dict[str, int]:
        raise RuntimeError("ciclo de execuções quebrado")

    def account_cycle(_self: AccountJobs) -> dict[str, int]:
        account_cycles.append(True)
        shutdown.set()
        return {}

    monkeypatch.setattr(Maintenance, "run_cycle", failing_cycle)
    monkeypatch.setattr(AccountJobs, "run_cycle", account_cycle)

    serve(
        db_settings.model_copy(update={"storage_root": tmp_path / "storage"}),
        local_runner(tmp_path / "work"),
        shutdown,
    )

    assert account_cycles == [True]
