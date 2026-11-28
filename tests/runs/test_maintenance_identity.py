"""Purga das contas em exclusão e retenção da identidade na manutenção (1.8; Frente C).

A manutenção roda os dois ciclos, o de execuções e arquivos (`Maintenance`) e o de contas
(`AccountJobs`), como `serve` faz; a remoção física dos arquivos é a da retenção normal.
"""

import hashlib
import itertools
import logging
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg import errors as psycopg_errors
from sqlalchemy import Engine, func, insert, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.maintenance import (
    AccountJobs,
    AccountJobsConfig,
    serve,
)
from goblinshift.entrypoints.shutdown import ShutdownFlag
from goblinshift.files import purge as files_purge
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import object_use, stored_object
from goblinshift.identity import IssuedKey, Scope, issue_key, request_account_deletion
from goblinshift.identity import purge as identity_purge
from goblinshift.identity.rate_limit import RequestClass, count_request
from goblinshift.identity.tables import (
    api_key,
    audit_event,
    legacy_request_window,
    login_state,
    web_session,
)
from goblinshift.platform.db import make_engine, make_session_factory, unit_of_work
from goblinshift.platform.settings import Settings
from goblinshift.runs import purge as runs_purge
from goblinshift.runs.lifecycle import Status
from goblinshift.runs.maintenance import Maintenance
from goblinshift.runs.purge import cancel_active_run
from goblinshift.runs.tables import attempt, run
from tests.files.support import create_intent
from tests.runs.maintenance_support import (
    MAX_CYCLES,
    count_owned,
    cycles_until_purged,
    keep_run,
    real_canceller,
)
from tests.runs.worker_support import claim, coordinator, create_user, local_runner, queue_run

pytestmark = pytest.mark.integration

# `statement_timeout`: o banco respondeu, então só o passo que o levou falha.
QUERY_CANCELED = "57014"
# Trava que o worker segura além do `lock_timeout` da conexão.
LOCK_NOT_AVAILABLE = "55P03"
# Falha que não é disputa com o worker: o papel sem a permissão.
INSUFFICIENT_PRIVILEGE = "42501"
# Curto para o teste não esperar os 2 s do `db_lock_timeout_ms` padrão.
LOCK_TIMEOUT_MS = 100
MAINTENANCE_LOG = "goblinshift.maintenance"
# Instante fixo dos testes da retenção da auditoria.
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def full_account(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path, subject: str
) -> tuple[uuid.UUID, IssuedKey]:
    """Conta com execução terminada (entradas, saídas, tentativa), upload incompleto, chave,
    sessão e janela do limite de requisições: tudo o que um uso real deixa no banco e no disco."""
    owner = create_user(factory, subject)
    queue_run(factory, storage, owner)
    coordinator(factory, storage, local_runner(work_root)).run(claim(factory, storage))
    create_intent(factory, owner)
    now = datetime.now(UTC)
    with unit_of_work(factory) as session:
        key = issue_key(session, owner_id=owner, scopes=frozenset(Scope), now=now)
        count_request(session, owner, "api_key", RequestClass.READ, now)
        # A janela sem canal da 0007, que a réplica anterior ao deploy ainda grava (preflight R4).
        session.execute(
            insert(legacy_request_window).values(
                owner_id=owner, request_class="read", window_start=now, request_count=1
            )
        )
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

    jobs = AccountJobs(factory, cancel_active_run=real_canceller(factory, storage))

    cycles = cycles_until_purged(factory, maintenance, jobs, owner)

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

    jobs = AccountJobs(factory, cancel_active_run=real_canceller(factory, storage))
    cycles_until_purged(factory, maintenance, jobs, owner)

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
    jobs = AccountJobs(
        factory,
        cancel_active_run=real_canceller(factory, storage),
        config=AccountJobsConfig(batch_size=1),
    )
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
    counts = jobs.run_cycle()
    assert counts["accounts_purge_failed"] == 1
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
    # Sem o cancelador da purga: a Run segue ativa até o cancelamento explícito abaixo.
    jobs = AccountJobs(factory, cancel_active_run=keep_run)

    maintenance.run_cycle()
    counts = jobs.run_cycle()

    assert counts["accounts_purged"] == 0
    assert count_owned(factory, owner)["run"] == 1

    result = cancel_active_run(factory, storage, owner, on_finished=None)
    assert result is not None
    assert result.status == Status.CANCELED
    cycles_until_purged(factory, maintenance, jobs, owner)


def test_conta_sem_pedido_nao_e_purgada(
    factory: sessionmaker[Session], storage: FileStorage, maintenance: Maintenance
) -> None:
    owner = create_user(factory, "ana")
    queue_run(factory, storage, owner)

    maintenance.run_cycle()
    AccountJobs(factory, cancel_active_run=real_canceller(factory, storage)).run_cycle()

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

    jobs = AccountJobs(factory, cancel_active_run=real_canceller(factory, storage))
    cycles_until_purged(factory, maintenance, jobs, owner)

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

        count_request(session, owner, "api_key", RequestClass.READ, now - timedelta(minutes=3))
        count_request(session, owner, "api_key", RequestClass.READ, now)

    counts = AccountJobs(factory, cancel_active_run=keep_run, clock=lambda: now).run_cycle()

    assert counts["login_states_removed"] == 1
    assert counts["web_sessions_removed"] == 1
    assert counts["audit_events_removed"] == 1
    assert counts["request_windows_removed"] == 1
    assert counts["request_windows"] == 1


def test_ciclo_de_contas_conta_api_keys_removed(factory: sessionmaker[Session]) -> None:
    now = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    owner = create_user(factory, "bia")
    with unit_of_work(factory) as session:
        # Venceu há 31 dias: passou da retenção. A ativa fica.
        issue_key(
            session, owner_id=owner, scopes=frozenset(Scope), now=now - timedelta(days=61), days=30
        )
        active = issue_key(session, owner_id=owner, scopes=frozenset(Scope), now=now, days=30)

    counts = AccountJobs(factory, cancel_active_run=keep_run, clock=lambda: now).run_cycle()

    assert counts["api_keys_removed"] == 1
    with factory() as session:
        left = set(
            session.execute(select(api_key.c.id).where(api_key.c.owner_id == owner)).scalars()
        )
    assert left == {active.id}


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
        counts = AccountJobs(factory, cancel_active_run=keep_run, clock=lambda: now).run_cycle()

    assert counts["accounts_deletion_overdue"] == 1
    alerts = [r for r in caplog.records if getattr(r, "fields", {}).get("alert")]
    assert [r.fields["alert"] for r in alerts] == ["account_deletion_overdue"]  # type: ignore[attr-defined]


def insert_audit_events(factory: sessionmaker[Session], *occurred_at: datetime) -> None:
    with unit_of_work(factory) as session:
        session.execute(
            insert(audit_event),
            [
                {"event_type": "LOGOUT", "outcome": "success", "occurred_at": moment}
                for moment in occurred_at
            ],
        )


def audit_events_left(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        left: int = session.execute(select(func.count()).select_from(audit_event)).scalar_one()
    return left


def logged_fields(record: logging.LogRecord) -> dict[str, object]:
    """Os campos de evento que o `extra={"fields": ...}` pôs no registro de log."""
    fields = vars(record)["fields"]
    assert isinstance(fields, dict)
    return fields


def advancing_clock(step_s: float) -> Callable[[], float]:
    """Relógio monotônico falso que anda `step_s` a cada leitura."""
    ticks = itertools.count(step=step_s)
    return lambda: float(next(ticks))


def test_um_ciclo_apaga_mais_que_um_lote(factory: sessionmaker[Session]) -> None:
    insert_audit_events(factory, *[NOW - timedelta(days=91)] * 5, NOW - timedelta(days=89))
    jobs = AccountJobs(
        factory,
        cancel_active_run=keep_run,
        clock=lambda: NOW,
        config=AccountJobsConfig(batch_size=2),
    )

    counts = jobs.run_cycle()

    assert counts["audit_events_removed"] == 5
    assert audit_events_left(factory) == 1


def test_retencao_para_no_prazo_e_continua_no_ciclo_seguinte(
    factory: sessionmaker[Session],
) -> None:
    insert_audit_events(factory, *[NOW - timedelta(days=91)] * 3)
    # Cada leitura passa o prazo de 20 s: só um lote por ciclo.
    jobs = AccountJobs(
        factory,
        cancel_active_run=keep_run,
        clock=lambda: NOW,
        config=AccountJobsConfig(batch_size=2),
        monotonic=advancing_clock(21),
    )

    first = jobs.run_cycle()
    second = jobs.run_cycle()

    assert (first["audit_events_removed"], first["account_steps_failed"]) == (2, 0)
    assert second["audit_events_removed"] == 1
    assert audit_events_left(factory) == 0


def test_falha_num_lote_do_meio_conta_as_remocoes_ja_confirmadas(
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    insert_audit_events(factory, *[NOW - timedelta(days=91)] * 5)
    real_delete = identity_purge.delete_old_audit_events
    calls = itertools.count(1)

    def failing_on_second(session: Session, at: datetime, limit: int) -> int:
        if next(calls) == 2:
            raise statement_timeout()
        return real_delete(session, at, limit)

    monkeypatch.setattr(identity_purge, "delete_old_audit_events", failing_on_second)
    jobs = AccountJobs(
        factory,
        cancel_active_run=keep_run,
        clock=lambda: NOW,
        config=AccountJobsConfig(batch_size=2),
    )

    with caplog.at_level(logging.ERROR):
        counts = jobs.run_cycle()

    assert (counts["audit_events_removed"], counts["account_steps_failed"]) == (2, 1)
    assert audit_events_left(factory) == 3
    [failure] = [r for r in caplog.records if r.getMessage() == "passo da manutenção falhou"]
    assert logged_fields(failure) == {
        "step": "audit_events_removed",
        "error_type": "OperationalError",
        "sqlstate": QUERY_CANCELED,
    }


@pytest.mark.parametrize("field", ["batch_size", "accounts_per_cycle"])
def test_configuracao_do_ciclo_de_contas_recusa_zero(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        AccountJobsConfig(**{field: 0})


def run_behind_cycle(factory: sessionmaker[Session]) -> dict[str, int]:
    """Uma volta com dois eventos vencidos há mais de um dia e só um lote dentro do prazo."""
    insert_audit_events(factory, *[NOW - timedelta(days=91, seconds=1)] * 2)
    jobs = AccountJobs(
        factory,
        cancel_active_run=keep_run,
        clock=lambda: NOW,
        config=AccountJobsConfig(batch_size=1),
        monotonic=advancing_clock(21),
    )
    return jobs.run_cycle()


def test_retencao_atrasada_remove_so_o_lote_que_coube_no_prazo(
    factory: sessionmaker[Session],
) -> None:
    counts = run_behind_cycle(factory)

    assert counts["audit_events_removed"] == 1


def test_retencao_atrasada_conta_o_atraso_e_a_estimativa(factory: sessionmaker[Session]) -> None:
    counts = run_behind_cycle(factory)

    assert counts["audit_events_overdue"] == 1
    assert counts["audit_events_estimated"] >= 0


def test_atraso_de_mais_de_um_dia_gera_o_alerta(
    factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="goblinshift.maintenance"):
        run_behind_cycle(factory)

    [alert] = [r for r in caplog.records if getattr(r, "fields", {}).get("alert")]
    assert logged_fields(alert) == {"alert": "audit_retention_behind", "events": 1}


def test_serve_roda_o_ciclo_de_contas_mesmo_se_o_de_execucoes_falhar(
    db_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutdown = ShutdownFlag()
    account_cycles: list[bool] = []

    def failing_cycle(_self: Maintenance, should_stop: Callable[[], bool]) -> dict[str, int]:
        raise RuntimeError("ciclo de execuções quebrado")

    def account_cycle(_self: AccountJobs) -> dict[str, int]:
        account_cycles.append(True)
        shutdown.request()
        return {}

    monkeypatch.setattr(Maintenance, "run_cycle", failing_cycle)
    monkeypatch.setattr(AccountJobs, "run_cycle", account_cycle)

    serve(
        db_settings.model_copy(update={"storage_root": tmp_path / "storage"}),
        local_runner(tmp_path / "work"),
        shutdown,
    )

    assert account_cycles == [True]


# --- Falhas isoladas no ciclo de contas ---------------------------------------------------------


def statement_timeout() -> Exception:
    driver_error = psycopg_errors.lookup(QUERY_CANCELED)("canceling statement due to timeout")
    return DBAPIError.instance("SELECT 1", {}, driver_error, psycopg.Error)


def connection_refused() -> Exception:
    """Como o SQLAlchemy entrega o `connect` recusado: `OperationalError` sem SQLSTATE."""
    driver_error = psycopg.OperationalError("connection refused")
    return DBAPIError.instance(None, None, driver_error, psycopg.Error)


def stale_identity_rows(factory: sessionmaker[Session], now: datetime) -> uuid.UUID:
    """Estado vencido de login e janela antiga, mais uma conta com exclusão atrasada."""
    owner = create_user(factory, "ana")
    with unit_of_work(factory) as session:
        session.execute(
            insert(login_state).values(key="k", value="{}", expires_at=now - timedelta(minutes=1))
        )
        count_request(session, owner, "api_key", RequestClass.READ, now - timedelta(minutes=3))
        request_account_deletion(session, owner, now - timedelta(hours=21))
    return owner


def test_falha_na_purga_nao_impede_limpezas_nem_alerta(
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    stale_identity_rows(factory, now)

    def timing_out(_session: Session, _limit: int) -> list[uuid.UUID]:
        raise statement_timeout()

    monkeypatch.setattr(identity_purge, "claim_accounts_for_purge", timing_out)

    with caplog.at_level(logging.WARNING):
        counts = AccountJobs(factory, cancel_active_run=keep_run, clock=lambda: now).run_cycle()

    assert counts["login_states_removed"] == 1
    assert counts["request_windows_removed"] == 1
    assert counts["accounts_deletion_overdue"] == 1
    assert counts["account_steps_failed"] == 1
    assert "accounts_purged" not in counts
    alerts = [r.fields["alert"] for r in caplog.records if "alert" in getattr(r, "fields", {})]  # type: ignore[attr-defined]
    assert alerts == ["account_deletion_overdue"]
    [failure] = [r for r in caplog.records if r.getMessage() == "passo da manutenção falhou"]
    assert failure.fields == {  # type: ignore[attr-defined]
        "step": "accounts_purged",
        "error_type": "OperationalError",
        "sqlstate": QUERY_CANCELED,
    }


def test_falha_na_purga_de_uma_conta_nao_impede_as_outras(
    factory: sessionmaker[Session],
    maintenance: Maintenance,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime.now(UTC)
    stuck = create_user(factory, "ana")
    other = create_user(factory, "bia")
    with unit_of_work(factory) as session:
        # A conta com defeito é a mais antiga: a primeira que a purga tenta.
        request_account_deletion(session, stuck, now - timedelta(hours=2))
        request_account_deletion(session, other, now - timedelta(hours=1))
    real_batch = runs_purge.purge_owner_batch

    def failing_for_stuck(session: Session, owner_id: uuid.UUID, limit: int) -> bool:
        if owner_id == stuck:
            raise statement_timeout()
        return real_batch(session, owner_id, limit)

    monkeypatch.setattr(runs_purge, "purge_owner_batch", failing_for_stuck)

    maintenance.run_cycle()
    with caplog.at_level(logging.ERROR, logger="goblinshift.maintenance"):
        counts = AccountJobs(factory, cancel_active_run=keep_run).run_cycle()

    assert count_owned(factory, other)["app_user"] == 0
    assert count_owned(factory, stuck)["app_user"] == 1
    assert (counts["accounts_purged"], counts["accounts_purge_failed"]) == (1, 1)
    assert counts["account_steps_failed"] == 1
    [failure] = [r for r in caplog.records if r.getMessage() == "purga da conta falhou"]
    assert failure.fields == {  # type: ignore[attr-defined]
        "user_id": str(stuck),
        "error_type": "OperationalError",
        "sqlstate": QUERY_CANCELED,
    }


def test_ciclo_de_contas_sem_falha_conta_zero_passos_falhos(factory: sessionmaker[Session]) -> None:
    counts = AccountJobs(factory, cancel_active_run=keep_run).run_cycle()

    assert (counts["account_steps_failed"], counts["accounts_purge_failed"]) == (0, 0)


def test_duracao_do_ciclo_de_contas_usa_o_relogio_injetado(
    factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    # Cada leitura do relógio falso anda 1 s: só ele dá uma duração em segundos inteiros.
    jobs = AccountJobs(factory, cancel_active_run=keep_run, monotonic=advancing_clock(1))

    with caplog.at_level(logging.INFO, logger="goblinshift.maintenance"):
        jobs.run_cycle()

    [cycle] = [r for r in caplog.records if r.getMessage() == "ciclo de contas"]
    duration_ms = logged_fields(cycle)["duration_ms"]
    assert isinstance(duration_ms, int)
    assert duration_ms > 0
    assert duration_ms % 1000 == 0


def test_banco_inalcancavel_ainda_derruba_o_ciclo_de_contas(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem conexão, cada passo seguinte só esperaria o `connect_timeout`: o ciclo cai inteiro."""

    def refused(_session: Session, _limit: int) -> list[uuid.UUID]:
        raise connection_refused()

    monkeypatch.setattr(identity_purge, "claim_accounts_for_purge", refused)

    with pytest.raises(DBAPIError):
        AccountJobs(factory, cancel_active_run=keep_run).run_cycle()


def test_banco_inalcancavel_na_purga_de_uma_conta_derruba_o_ciclo(
    factory: sessionmaker[Session],
    maintenance: Maintenance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A conexão caiu no meio da purga: as contas seguintes não são tentadas, e o ciclo cai."""
    now = datetime.now(UTC)
    first = create_user(factory, "ana")
    second = create_user(factory, "bia")
    with unit_of_work(factory) as session:
        request_account_deletion(session, first, now - timedelta(hours=2))
        request_account_deletion(session, second, now - timedelta(hours=1))
    real_batch = runs_purge.purge_owner_batch
    attempted: list[uuid.UUID] = []

    def refused_for_first(session: Session, owner_id: uuid.UUID, limit: int) -> bool:
        attempted.append(owner_id)
        if owner_id == first:
            raise connection_refused()
        return real_batch(session, owner_id, limit)

    monkeypatch.setattr(runs_purge, "purge_owner_batch", refused_for_first)
    maintenance.run_cycle()

    with pytest.raises(DBAPIError):
        AccountJobs(factory, cancel_active_run=keep_run).run_cycle()

    assert attempted == [first]
    assert count_owned(factory, second)["app_user"] == 1


class FailingOnce:
    """Cancelador que falha com `statement_timeout` na primeira chamada e depois cancela."""

    def __init__(self, real: Callable[[uuid.UUID], object]) -> None:
        self._real = real
        self.calls = 0

    def __call__(self, owner_id: uuid.UUID) -> object:
        self.calls += 1
        if self.calls == 1:
            raise statement_timeout()
        return self._real(owner_id)


def test_statement_timeout_no_cancelamento_da_purga_conta_e_fica_no_log_fora_do_alerta(
    factory: sessionmaker[Session],
    storage: FileStorage,
    maintenance: Maintenance,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#442: a Run que o `DELETE /me` não cancelou é cancelada pela purga; um statement timeout
    (57014) no cancelamento conta só em `accounts_cancel_failed` e fica no log. Não conta em
    `account_steps_failed`: é disputa passageira com o worker e se resolve no ciclo seguinte."""
    owner = create_user(factory, "ana")
    queue_run(factory, storage, owner)
    request_deletion(factory, owner)
    jobs = AccountJobs(factory, cancel_active_run=FailingOnce(real_canceller(factory, storage)))
    maintenance.run_cycle()

    with caplog.at_level(logging.ERROR, logger="goblinshift.maintenance"):
        counts = jobs.run_cycle()

    assert (counts["accounts_cancel_failed"], counts["account_steps_failed"]) == (1, 0)
    assert count_owned(factory, owner)["app_user"] == 1
    [failure] = [r for r in caplog.records if r.getMessage() == "purga: cancelamento falhou"]
    assert failure.fields == {  # type: ignore[attr-defined]
        "user_id": str(owner),
        "error_type": "OperationalError",
        "sqlstate": QUERY_CANCELED,
    }


def insufficient_privilege() -> Exception:
    driver_error = psycopg_errors.lookup(INSUFFICIENT_PRIVILEGE)("permission denied for table run")
    return DBAPIError.instance("UPDATE run", {}, driver_error, psycopg.Error)


@pytest.mark.parametrize(
    "error",
    [RuntimeError("bug no cancelamento"), insufficient_privilege()],
    ids=["bug", "sem-permissao"],
)
def test_cancelamento_falho_sem_disputa_de_trava_poe_a_volta_no_alerta(
    error: Exception,
    factory: sessionmaker[Session],
    storage: FileStorage,
    maintenance: Maintenance,
) -> None:
    """#442: só a disputa com o worker (55P03, 57014) fica fora do alerta. Um bug ou um `42501`
    no cancelamento conta em `accounts_cancel_failed` e também em `account_steps_failed`."""
    owner = create_user(factory, "ana")
    queue_run(factory, storage, owner)
    request_deletion(factory, owner)

    def broken(_owner_id: uuid.UUID) -> object:
        raise error

    maintenance.run_cycle()
    counts = AccountJobs(factory, cancel_active_run=broken).run_cycle()

    assert (counts["accounts_cancel_failed"], counts["account_steps_failed"]) == (1, 1)


def test_cancelamento_falho_na_purga_tenta_de_novo_no_ciclo_seguinte(
    factory: sessionmaker[Session], storage: FileStorage, maintenance: Maintenance
) -> None:
    owner = create_user(factory, "ana")
    queue_run(factory, storage, owner)
    request_deletion(factory, owner)
    canceller = FailingOnce(real_canceller(factory, storage))
    jobs = AccountJobs(factory, cancel_active_run=canceller)

    cycles = cycles_until_purged(factory, maintenance, jobs, owner)

    assert canceller.calls >= 2
    assert cycles >= 2
    assert set(count_owned(factory, owner).values()) == {0}


def test_worker_segurando_a_run_adia_o_cancelamento_da_purga_sem_derrubar_o_ciclo(
    engine: Engine,
    db_settings: Settings,
    factory: sessionmaker[Session],
    storage: FileStorage,
    maintenance: Maintenance,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#442: a corrida real com o worker. Outra conexão segura a Run (`FOR UPDATE`), e o
    cancelamento da purga desiste no `lock_timeout` (55P03): a conta fica para o ciclo seguinte,
    sem derrubar o ciclo nem pôr a volta no alerta. Solta a trava, a purga acontece."""
    owner = create_user(factory, "ana")
    run_id = queue_run(factory, storage, owner)
    request_deletion(factory, owner)
    impatient = make_engine(db_settings.model_copy(update={"db_lock_timeout_ms": LOCK_TIMEOUT_MS}))
    try:
        short_lock = make_session_factory(impatient)
        jobs = AccountJobs(short_lock, cancel_active_run=real_canceller(short_lock, storage))
        maintenance.run_cycle()

        with engine.connect() as worker, caplog.at_level(logging.ERROR, logger=MAINTENANCE_LOG):
            worker.execute(select(run.c.id).where(run.c.id == run_id).with_for_update())
            counts = jobs.run_cycle()
            worker.rollback()

        assert counts["accounts_cancel_failed"] == 1
        assert (counts["account_steps_failed"], counts["accounts_purged"]) == (0, 0)
        [failure] = [r for r in caplog.records if r.getMessage() == "purga: cancelamento falhou"]
        assert failure.fields["sqlstate"] == LOCK_NOT_AVAILABLE  # type: ignore[attr-defined]
        with factory() as session:
            status = session.execute(select(run.c.status).where(run.c.id == run_id)).scalar_one()
        assert status == Status.QUEUED

        cycles_until_purged(factory, maintenance, jobs, owner)
    finally:
        impatient.dispose()

    assert set(count_owned(factory, owner).values()) == {0}


def test_banco_inalcancavel_no_cancelamento_da_purga_derruba_o_ciclo(
    factory: sessionmaker[Session], storage: FileStorage, maintenance: Maintenance
) -> None:
    owner = create_user(factory, "ana")
    queue_run(factory, storage, owner)
    request_deletion(factory, owner)

    def refused(_owner_id: uuid.UUID) -> object:
        raise connection_refused()

    maintenance.run_cycle()
    with pytest.raises(DBAPIError):
        AccountJobs(factory, cancel_active_run=refused).run_cycle()

    assert count_owned(factory, owner)["run"] == 1


def test_contas_com_falha_permanente_na_frente_nao_impedem_a_purga_das_seguintes(
    factory: sessionmaker[Session],
    maintenance: Maintenance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#442: o lote inteiro de `accounts_per_cycle` contas mais antigas falha sempre."""
    per_cycle = 2
    now = datetime.now(UTC)
    stuck = [create_user(factory, f"presa-{index}") for index in range(per_cycle)]
    healthy = create_user(factory, "sa")
    with unit_of_work(factory) as session:
        for index, owner in enumerate(stuck):
            request_account_deletion(session, owner, now - timedelta(hours=3, minutes=index))
        request_account_deletion(session, healthy, now - timedelta(hours=1))
    real_batch = runs_purge.purge_owner_batch

    def failing_for_stuck(session: Session, owner_id: uuid.UUID, limit: int) -> bool:
        if owner_id in stuck:
            raise statement_timeout()
        return real_batch(session, owner_id, limit)

    monkeypatch.setattr(runs_purge, "purge_owner_batch", failing_for_stuck)
    jobs = AccountJobs(
        factory,
        cancel_active_run=keep_run,
        config=AccountJobsConfig(accounts_per_cycle=per_cycle),
    )

    for _ in range(MAX_CYCLES):
        maintenance.run_cycle()
        jobs.run_cycle()

    assert count_owned(factory, healthy)["app_user"] == 0
