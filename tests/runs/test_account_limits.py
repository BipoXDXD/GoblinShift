"""Cotas por conta: padrão das settings, override do administrador e tempo de executor (1.7, 1.8).

O override vale para a conta inteira até ser desfeito; sem ele valem os padrões das settings.
O tempo de executor é derivado das tentativas (3.3: sem tabela de contadores) e cobre a mesma
janela de 24 h das cotas: tentativas que puseram o executor para trabalhar nesse intervalo, com a
viva contada até agora.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.settings import LOCAL_EXECUTOR_IMAGE, Settings
from goblinshift.runs.acceptance import DailyQuotaExceededError, accept_run
from goblinshift.runs.quotas import (
    DEFAULT_QUOTA,
    QuotaLimits,
    executor_time_ms,
    quota_for,
    set_account_quota,
)
from goblinshift.runs.tables import attempt
from tests.runs.support import (
    insert_finished_run,
    new_key,
    new_owner,
    ready_inputs,
    run_command,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
GIB = 1024**3
CUSTOM = QuotaLimits(runs_per_24h=50, input_bytes_per_24h=3 * GIB)


def quota_of(
    factory: sessionmaker[Session], owner: uuid.UUID, defaults: QuotaLimits
) -> QuotaLimits:
    with factory() as session:
        return quota_for(session, owner, defaults)


# --- Padrão e override -------------------------------------------------------------------------


def test_padrao_de_1_7_e_o_das_settings() -> None:
    assert QuotaLimits(runs_per_24h=30, input_bytes_per_24h=2 * GIB) == DEFAULT_QUOTA
    assert QuotaLimits.from_settings(Settings(environment="test")) == DEFAULT_QUOTA


def test_settings_tipadas_mudam_o_padrao(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GS_QUOTA_RUNS_PER_24H", "50")
    monkeypatch.setenv("GS_QUOTA_INPUT_BYTES_PER_24H", str(3 * GIB))

    assert QuotaLimits.from_settings(Settings(environment="test")) == CUSTOM


@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_settings_recusam_cota_nao_positiva(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("GS_QUOTA_RUNS_PER_24H", value)

    with pytest.raises(ValueError, match="quota_runs_per_24h"):
        Settings(environment="test")


def test_conta_sem_override_usa_o_padrao_recebido(factory: sessionmaker[Session]) -> None:
    assert quota_of(factory, new_owner(factory), CUSTOM) == CUSTOM


def test_override_parcial_troca_so_o_que_foi_definido(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    with unit_of_work(factory) as session:
        set_account_quota(session, owner, runs_per_24h=5, input_bytes_per_24h=None)

    assert quota_of(factory, owner, CUSTOM) == QuotaLimits(5, 3 * GIB)


def test_override_desfeito_volta_ao_padrao(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    with unit_of_work(factory) as session:
        set_account_quota(session, owner, runs_per_24h=5, input_bytes_per_24h=GIB)
    with unit_of_work(factory) as session:
        set_account_quota(session, owner, runs_per_24h=None, input_bytes_per_24h=None)

    assert quota_of(factory, owner, CUSTOM) == CUSTOM


def test_override_de_outra_conta_nao_vaza(factory: sessionmaker[Session]) -> None:
    other = new_owner(factory)
    with unit_of_work(factory) as session:
        set_account_quota(session, other, runs_per_24h=5, input_bytes_per_24h=GIB)

    assert quota_of(factory, new_owner(factory), CUSTOM) == CUSTOM


def test_banco_recusa_override_zero(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)

    with pytest.raises(IntegrityError), unit_of_work(factory) as session:
        set_account_quota(session, owner, runs_per_24h=0, input_bytes_per_24h=None)


def test_aceitacao_aplica_o_override_da_conta(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    with unit_of_work(factory) as session:
        set_account_quota(session, owner, runs_per_24h=2, input_bytes_per_24h=None)
    for hours in (1, 2):
        insert_finished_run(factory, owner, datetime.now(UTC) - timedelta(hours=hours))
    command = run_command(ready_inputs(factory, storage, owner))

    with pytest.raises(DailyQuotaExceededError), factory() as session:
        accept_run(
            session,
            owner,
            command,
            new_key(),
            None,
            executor_image=LOCAL_EXECUTOR_IMAGE,
            quota_defaults=DEFAULT_QUOTA,
        )


def test_aceitacao_aplica_o_padrao_das_settings(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    insert_finished_run(factory, owner, datetime.now(UTC) - timedelta(hours=1))
    command = run_command(ready_inputs(factory, storage, owner))

    with pytest.raises(DailyQuotaExceededError), factory() as session:
        accept_run(
            session,
            owner,
            command,
            new_key(),
            None,
            executor_image=LOCAL_EXECUTOR_IMAGE,
            quota_defaults=QuotaLimits(runs_per_24h=1, input_bytes_per_24h=GIB),
        )


# --- Tempo de executor -------------------------------------------------------------------------


def add_attempt(
    factory: sessionmaker[Session],
    run_id: uuid.UUID,
    number: int,
    started: datetime | None,
    ended: datetime | None,
) -> None:
    with unit_of_work(factory) as session:
        session.execute(
            insert(attempt).values(
                run_id=run_id,
                number=number,
                generation=number,
                container_name=f"gs-{uuid.uuid4()}",
                lease_expires_at=NOW,
                executor_started_at=started,
                ended_at=ended,
            )
        )


def executor_ms(factory: sessionmaker[Session], owner: uuid.UUID) -> int:
    with factory() as session:
        return executor_time_ms(session, owner, NOW)


def test_tempo_de_executor_soma_as_tentativas_da_janela(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    run_id = insert_finished_run(factory, owner, NOW - timedelta(hours=2))
    add_attempt(factory, run_id, 1, NOW - timedelta(minutes=10), NOW - timedelta(minutes=5))
    add_attempt(factory, run_id, 2, NOW - timedelta(minutes=4), NOW - timedelta(minutes=3))

    assert executor_ms(factory, owner) == 6 * 60_000


def test_tentativa_viva_conta_ate_agora(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    run_id = insert_finished_run(factory, owner, NOW - timedelta(hours=2))
    add_attempt(factory, run_id, 1, NOW - timedelta(minutes=2), None)

    assert executor_ms(factory, owner) == 2 * 60_000


@pytest.mark.parametrize(
    ("started_ago", "counted"),
    [
        (timedelta(hours=24), False),  # fronteira: a janela é (agora - 24 h, agora]
        (timedelta(hours=24) - timedelta(seconds=1), True),
    ],
)
def test_janela_de_24_h_do_tempo_de_executor(
    factory: sessionmaker[Session], started_ago: timedelta, counted: bool
) -> None:
    owner = new_owner(factory)
    run_id = insert_finished_run(factory, owner, NOW - timedelta(days=2))
    add_attempt(factory, run_id, 1, NOW - started_ago, NOW - started_ago + timedelta(seconds=1))

    assert executor_ms(factory, owner) == (1000 if counted else 0)


def test_claim_que_nao_chegou_ao_executor_e_outra_conta_nao_contam(
    factory: sessionmaker[Session],
) -> None:
    owner = new_owner(factory)
    own_run = insert_finished_run(factory, owner, NOW - timedelta(hours=2))
    add_attempt(factory, own_run, 1, None, NOW - timedelta(minutes=1))
    other_run = insert_finished_run(factory, new_owner(factory), NOW - timedelta(hours=2))
    add_attempt(factory, other_run, 1, NOW - timedelta(minutes=9), NOW - timedelta(minutes=1))

    assert executor_ms(factory, owner) == 0
