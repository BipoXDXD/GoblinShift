"""Peças do span `attempt` sem banco: tempos do executor e link para a aceitação (3.7).

O `telemetry.json` vem do sandbox: tudo o que foge do schema é descartado inteiro, e a tentativa
segue sem os tempos.
"""

import json
import os
from pathlib import Path

import pytest

from goblinshift.runs.worker.attempt_event import (
    TIMINGS_MAX_BYTES,
    links_to_acceptance,
    read_executor_timings,
)

TRACE_ID = 0x4BF92F3577B34DA6A3CE929D0E0E4736
SPAN_ID = 0x00F067AA0BA902B7
TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def write(path: Path, document: object) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# --- telemetry.json ----------------------------------------------------------------------------


def test_le_as_tres_fases(tmp_path: Path) -> None:
    path = write(
        tmp_path / "telemetry.json",
        {"schema_version": 1, "inspect_ms": 12, "process_ms": 340, "package_ms": 0},
    )

    assert read_executor_timings(path) == {"inspect_ms": 12, "process_ms": 340, "package_ms": 0}


def test_le_so_as_fases_presentes(tmp_path: Path) -> None:
    path = write(tmp_path / "telemetry.json", {"schema_version": 1, "inspect_ms": 7})

    assert read_executor_timings(path) == {"inspect_ms": 7}


def test_arquivo_ausente_nao_tem_tempos(tmp_path: Path) -> None:
    assert read_executor_timings(tmp_path / "telemetry.json") == {}


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": 1, "inspect_ms": -1},
        {"schema_version": 1, "inspect_ms": 1.5},
        {"schema_version": 1, "inspect_ms": True},
        {"schema_version": 1, "inspect_ms": "12"},
        {"schema_version": 1, "inspect_ms": 3, "original_name": "a.csv"},
        {"schema_version": 2, "inspect_ms": 3},
        {"inspect_ms": 3},
        [1, 2, 3],
    ],
    ids=["negativo", "fracao", "booleano", "texto", "chave-extra", "versao", "sem-versao", "lista"],
)
def test_documento_fora_do_schema_e_descartado_inteiro(tmp_path: Path, document: object) -> None:
    path = write(tmp_path / "telemetry.json", document)

    assert read_executor_timings(path) == {}


def test_json_invalido_e_descartado(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.json"
    path.write_text('{"schema_version": 1, "inspect_ms": 3, "inspect_ms": 4}', encoding="utf-8")

    assert read_executor_timings(path) == {}


def test_arquivo_acima_do_teto_e_descartado(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.json"
    padding = " " * TIMINGS_MAX_BYTES
    path.write_text(f'{{"schema_version": 1, "inspect_ms": 3}}{padding}', encoding="utf-8")

    assert read_executor_timings(path) == {}


def test_symlink_nao_e_seguido(tmp_path: Path) -> None:
    target = write(tmp_path / "alvo.json", {"schema_version": 1, "inspect_ms": 3})
    link = tmp_path / "telemetry.json"
    link.symlink_to(target)

    assert read_executor_timings(link) == {}


def test_fifo_nao_trava_a_leitura(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.json"
    os.mkfifo(path)

    assert read_executor_timings(path) == {}


# --- link para o trace da aceitação ---------------------------------------------------------------


def test_traceparent_valido_vira_um_link() -> None:
    [link] = links_to_acceptance(TRACEPARENT)

    assert link.context.trace_id == TRACE_ID
    assert link.context.span_id == SPAN_ID
    assert link.context.is_remote is True


@pytest.mark.parametrize(
    "traceparent",
    [None, "", "lixo", "00-00000000000000000000000000000000-00f067aa0ba902b7-01"],
    ids=["ausente", "vazio", "lixo", "trace-zerado"],
)
def test_traceparent_ausente_ou_invalido_fica_sem_link(traceparent: str | None) -> None:
    assert links_to_acceptance(traceparent) == []
