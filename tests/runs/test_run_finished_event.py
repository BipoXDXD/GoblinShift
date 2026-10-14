"""Atributos do evento de término da Run e o contrato com o SLO de execução (3.7).

Sem banco: os fatos da Run entram prontos. O contrato confere que toda coluna que
`deploy/openobserve/sql/execucao.eventos.sql` lê sai do span com o nome que o OpenObserve dá a
ela (o `.` do atributo vira `_`).
"""

import re
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from goblinshift.platform.telemetry import UserHasher
from goblinshift.runs.finished import SPAN_NAME, FinishedRun
from goblinshift.runs.lifecycle import Status

EXECUTION_SQL = Path(__file__).parents[2] / "deploy/openobserve/sql/execucao.eventos.sql"
HASHER = UserHasher(b"chave-de-teste-do-user-hash-com-32-caracteres")
OWNER = uuid.UUID("01890000-0000-7000-8000-00000000abcd")
ACCEPTED = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
MIB = 1024 * 1024

SUCCEEDED = FinishedRun(
    run_id=uuid.UUID("01890000-0000-7000-8000-000000000001"),
    owner_id=OWNER,
    recipe_id="csv-janitor",
    recipe_version=1,
    status=Status.SUCCEEDED,
    error_code=None,
    attempts=1,
    input_count=2,
    input_bytes=3 * MIB,
    accepted_at=ACCEPTED,
    started_at=ACCEPTED + timedelta(seconds=4),
    finished_at=ACCEPTED + timedelta(seconds=4, milliseconds=2500),
)
QUEUE_EXPIRED = replace(
    SUCCEEDED,
    status=Status.FAILED,
    error_code="QUEUE_EXPIRED",
    attempts=0,
    started_at=None,
    finished_at=ACCEPTED + timedelta(hours=2, seconds=1),
)


def test_run_terminada_com_sucesso_tem_o_evento_completo_sem_error_code() -> None:
    attributes = SUCCEEDED.attributes(HASHER)

    assert attributes == {
        "main": True,
        "run_id": "01890000-0000-7000-8000-000000000001",
        "recipe_id": "csv-janitor",
        "recipe_version": 1,
        "user.hash": HASHER.hash(OWNER),
        "run.state": "succeeded",
        "run.attempts": 1,
        "run.input_count": 2,
        "run.duration_ms": 2500,
        "run.queue_wait_ms": 4000,
        "slo.execution.eligible": True,
    }


def test_evento_nunca_traz_o_owner_id() -> None:
    text = repr(QUEUE_EXPIRED.attributes(HASHER)) + repr(SUCCEEDED.attributes(HASHER))

    assert str(OWNER) not in text
    assert OWNER.hex not in text


def test_run_que_nunca_comecou_nao_tem_duracao_nem_espera_e_traz_o_codigo() -> None:
    attributes = QUEUE_EXPIRED.attributes(HASHER)

    assert attributes["run.state"] == "failed"
    assert attributes["error.code"] == "QUEUE_EXPIRED"
    assert "run.duration_ms" not in attributes
    assert "run.queue_wait_ms" not in attributes
    # Elegível e ruim: QUEUE_EXPIRED é falha de infraestrutura no SLO de execução.
    assert attributes["slo.execution.eligible"] is True


@pytest.mark.parametrize(
    ("changes", "eligible"),
    [
        ({"input_count": 10}, True),
        ({"input_count": 11}, False),
        ({"input_bytes": 50 * MIB}, True),
        ({"input_bytes": 50 * MIB + 1}, False),
        ({"input_bytes": None}, False),
        ({"status": Status.CANCELED, "error_code": "CANCELED"}, False),
        ({"status": Status.FAILED, "error_code": "DEADLINE_EXCEEDED"}, True),
        ({"status": Status.SUCCEEDED_WITH_WARNINGS}, True),
    ],
)
def test_elegibilidade_do_slo_de_execucao(changes: dict[str, Any], eligible: bool) -> None:
    finished = replace(SUCCEEDED, **changes)

    assert finished.attributes(HASHER)["slo.execution.eligible"] is eligible


# Colunas que a consulta do SLO de execução lê, no nome que o OpenObserve dá a cada atributo.
SQL_COLUMNS = {
    "main": "main",
    "slo_execution_eligible": "slo.execution.eligible",
    "error_code": "error.code",
    "run_duration_ms": "run.duration_ms",
}


def test_consulta_do_slo_le_exatamente_as_colunas_do_contrato() -> None:
    sql = _without_comments(EXECUTION_SQL.read_text())

    identifiers = set(re.findall(r"\b[a-z][a-z0-9_]*\b", sql))

    assert set(SQL_COLUMNS) <= identifiers
    assert f"operation_name = '{SPAN_NAME}'" in sql


@pytest.mark.parametrize("finished", [SUCCEEDED, QUEUE_EXPIRED], ids=["sucesso", "fila"])
def test_span_tem_cada_coluna_que_a_consulta_le(finished: FinishedRun) -> None:
    attributes = finished.attributes(HASHER)
    columns = {name.replace(".", "_") for name in attributes}

    expected = set(SQL_COLUMNS) - ({"error_code"} if finished.error_code is None else set())
    if finished.started_at is None:
        expected.discard("run_duration_ms")
    assert expected <= columns
    # O OpenObserve guarda os atributos como texto; a consulta compara com 'true'.
    assert attributes["main"] is True


def _without_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
