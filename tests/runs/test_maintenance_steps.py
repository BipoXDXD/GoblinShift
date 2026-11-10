"""Passos do ciclo da manutenção isolados entre si (triagem lote 2, L2).

Sem banco: os passos são trocados por funções falsas na instância. Uma exceção num passo (o
banco caiu no meio da reconciliação, por exemplo) não pode levar junto prazo, retenção, expurgo e
medidores do mesmo ciclo.
"""

import json
import logging
from collections.abc import Callable
from typing import cast

import psycopg
import pytest
from psycopg import errors as psycopg_errors
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.platform.telemetry import JsonFormatter, omit_database_error_detail
from goblinshift.runs.maintenance import STEPS_FAILED, Maintenance
from goblinshift.runs.worker.runner import Runner

MAINTENANCE_LOGGER = "goblinshift.runs.maintenance"
STEP_FAILED_MESSAGE = "passo da manutenção falhou"
DB_CANARY = "CANARY-host-db-interno"
QUERY_CANCELED = "57014"

# Cada passo do ciclo e uma contagem que só ele devolve.
STEPS: dict[str, dict[str, int]] = {
    "reconcile_expired_leases": {"leases_reconciled": 1},
    "expire_queue": {"queue_expired": 2},
    "apply_deadlines": {"deadlines": 3},
    "release_orphan_holds": {"holds_released": 4},
    "discard_stale_candidates": {"candidates_discarded": 5},
    "sweep_work_areas": {"work_areas_removed": 6},
    "apply_retention": {"objects_purged": 7},
    "sweep_temporary_parts": {"tmp_parts_removed": 8},
    "mark_missing_objects": {"objects_marked_missing": 9},
    "measure": {"runs_stuck_stopping": 10},
}
# Passos que devolvem um número em vez de um dicionário.
INT_STEPS = {
    "reconcile_expired_leases",
    "expire_queue",
    "apply_deadlines",
    "release_orphan_holds",
    "sweep_work_areas",
}


def database_error(sqlstate: str = QUERY_CANCELED) -> Exception:
    driver_error = psycopg_errors.lookup(sqlstate)(f'connection to server at "{DB_CANARY}"')
    return DBAPIError.instance("SELECT 1", {}, driver_error, psycopg.Error)


def _returning(name: str) -> Callable[[], object]:
    counts = STEPS[name]
    if name in INT_STEPS:
        [value] = counts.values()
        return lambda: value
    return lambda: dict(counts)


def _raising(error: BaseException) -> Callable[[], object]:
    def fail() -> object:
        raise error

    return fail


@pytest.fixture
def maintenance(monkeypatch: pytest.MonkeyPatch) -> Maintenance:
    janitor = Maintenance(
        cast(sessionmaker[Session], None), cast(FileStorage, None), cast(Runner, None)
    )
    for name in STEPS:
        monkeypatch.setattr(janitor, name, _returning(name))
    return janitor


def _expected_without(*failed: str) -> dict[str, int]:
    expected: dict[str, int] = {}
    for name, counts in STEPS.items():
        if name not in failed:
            expected.update(counts)
    return expected


def test_ciclo_sem_falha_conta_zero_passos_falhos(maintenance: Maintenance) -> None:
    counts = maintenance.run_cycle()

    assert counts == {
        **_expected_without(),
        "candidates_left_on_disk": 0,
        STEPS_FAILED: 0,
    }


@pytest.mark.parametrize("failing", list(STEPS))
def test_passo_que_levanta_nao_impede_os_outros(
    maintenance: Maintenance, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    monkeypatch.setattr(maintenance, failing, _raising(RuntimeError("quebrou")))

    counts = maintenance.run_cycle()

    assert counts == {
        **_expected_without(failing),
        "candidates_left_on_disk": 0,
        STEPS_FAILED: 1,
    }


def test_cada_passo_que_falha_sobe_o_contador(
    maintenance: Maintenance, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(maintenance, "reconcile_expired_leases", _raising(database_error()))
    monkeypatch.setattr(maintenance, "apply_retention", _raising(OSError("disco")))

    counts = maintenance.run_cycle()

    assert counts[STEPS_FAILED] == 2
    assert counts["deadlines"] == 3
    assert counts["runs_stuck_stopping"] == 10


def test_log_do_passo_traz_nome_tipo_e_sqlstate(
    maintenance: Maintenance,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(maintenance, "reconcile_expired_leases", _raising(database_error()))

    with caplog.at_level(logging.ERROR, logger=MAINTENANCE_LOGGER):
        maintenance.run_cycle()

    [record] = [r for r in caplog.records if r.getMessage() == STEP_FAILED_MESSAGE]
    assert record.fields == {  # type: ignore[attr-defined]
        "step": "reconcile_expired_leases",
        "error_type": "OperationalError",
        "sqlstate": QUERY_CANCELED,
    }


def test_log_do_passo_sem_banco_tem_sqlstate_nulo(
    maintenance: Maintenance,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(maintenance, "measure", _raising(OSError("disco")))

    with caplog.at_level(logging.ERROR, logger=MAINTENANCE_LOGGER):
        maintenance.run_cycle()

    [record] = [r for r in caplog.records if r.getMessage() == STEP_FAILED_MESSAGE]
    assert record.fields == {  # type: ignore[attr-defined]
        "step": "measure",
        "error_type": "OSError",
        "sqlstate": None,
    }


def test_log_do_passo_formatado_nao_traz_a_mensagem_do_banco(
    maintenance: Maintenance,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(maintenance, "apply_deadlines", _raising(database_error()))

    with caplog.at_level(logging.ERROR, logger=MAINTENANCE_LOGGER):
        maintenance.run_cycle()

    [record] = [r for r in caplog.records if r.getMessage() == STEP_FAILED_MESSAGE]
    # O mesmo caminho do processo: filtro de configure_logging e formatador JSON.
    assert omit_database_error_detail(record)
    line = JsonFormatter().format(record)
    assert DB_CANARY not in line
    assert json.loads(line)["step"] == "apply_deadlines"


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt(), SystemExit(1)])
def test_interrupcao_do_processo_nao_e_engolida(
    maintenance: Maintenance, monkeypatch: pytest.MonkeyPatch, interrupt: BaseException
) -> None:
    ran: list[str] = []

    def deadlines() -> int:
        ran.append("prazo")
        return 0

    monkeypatch.setattr(maintenance, "expire_queue", _raising(interrupt))
    monkeypatch.setattr(maintenance, "apply_deadlines", deadlines)

    with pytest.raises(type(interrupt)):
        maintenance.run_cycle()

    assert ran == []
