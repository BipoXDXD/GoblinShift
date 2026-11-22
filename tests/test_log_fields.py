"""Campos do evento estruturado passam por allowlist no `JsonFormatter` (3.7; #442).

Sem allowlist, qualquer chave nova em `extra={"fields": …}` levaria o texto que carrega, como um
nome de arquivo, ao log de produção. Chave fora de `LOGGED_FIELDS` só passa com número ou
booleano; as outras caem e a linha conta quantas em `dropped_fields`.
"""

import io
import json
import logging
import math
import uuid
from typing import Any, cast

import pytest

from goblinshift.platform.telemetry import LOGGED_FIELDS

RUN = str(uuid.uuid4())

type ProductionLog = tuple[logging.Logger, io.StringIO]


def _log(log: ProductionLog, *, fields: dict[str, object]) -> dict[str, Any]:
    logger, stream = log
    logger.warning("evento", extra={"fields": fields})
    [line] = stream.getvalue().splitlines()
    entry: dict[str, Any] = json.loads(line)
    return entry


def test_string_com_chave_desconhecida_cai_e_e_contada(
    production_log: ProductionLog, canary: str
) -> None:
    line = _log(production_log, fields={"run_id": RUN, "nome_do_arquivo": canary})

    assert line["run_id"] == RUN
    assert "nome_do_arquivo" not in line
    assert line["dropped_fields"] == 1
    assert canary not in json.dumps(line)


def test_numero_com_chave_desconhecida_passa(production_log: ProductionLog) -> None:
    line = _log(production_log, fields={"runs_released": 3, "ratio": 0.5, "ok": True})

    assert line["runs_released"] == 3
    assert line["ratio"] == 0.5
    assert line["ok"] is True
    assert "dropped_fields" not in line


def test_lista_dict_ou_none_com_chave_desconhecida_cai(
    production_log: ProductionLog, canary: str
) -> None:
    line = _log(production_log, fields={"x": [canary], "y": {"k": canary}, "z": None})

    assert {"x", "y", "z"}.isdisjoint(line)
    assert line["dropped_fields"] == 3
    assert canary not in production_log[1].getvalue()


def test_numero_com_texto_livre_no_nome_da_chave_cai(
    production_log: ProductionLog, canary: str
) -> None:
    # O nome da chave também vai ao log: um `{nome_do_arquivo: 1}` levaria o nome adiante.
    line = _log(production_log, fields={canary: 1, "Maiuscula": 2, "a" * 65: 3, "ok_1.b": 4})

    assert line["ok_1.b"] == 4
    assert line["dropped_fields"] == 3
    assert canary not in production_log[1].getvalue()


def test_chave_que_nao_e_texto_cai(production_log: ProductionLog) -> None:
    line = _log(production_log, fields=cast(dict[str, object], {7: 1}))

    assert "7" not in line
    assert line["dropped_fields"] == 1


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_numero_nao_finito_cai_mesmo_na_allowlist(
    production_log: ProductionLog, value: float
) -> None:
    line = _log(production_log, fields={"ratio": value, "duration_ms": value})

    assert {"ratio", "duration_ms"}.isdisjoint(line)
    assert line["dropped_fields"] == 2


def test_inteiro_fora_de_64_bits_cai_sem_derrubar_a_linha(production_log: ProductionLog) -> None:
    # Acima de 4300 dígitos o `json.dumps` levanta; acima de 64 bits já não é contagem.
    line = _log(
        production_log,
        fields={
            "duration_ms": 10**5000,
            "objects": 2**64,
            "checked": 2**64 - 1,
            "absent": -(2**63),
        },
    )

    assert {"duration_ms", "objects"}.isdisjoint(line)
    assert line["checked"] == 2**64 - 1
    assert line["absent"] == -(2**63)
    assert line["dropped_fields"] == 2


def test_chave_da_allowlist_passa_com_qualquer_valor(production_log: ProductionLog) -> None:
    line = _log(production_log, fields={"errnos": {"EIO": 2}, "error_reason": "busy"})

    assert line["errnos"] == {"EIO": 2}
    assert line["error_reason"] == "busy"


def test_campo_nao_sobrescreve_a_base(production_log: ProductionLog, canary: str) -> None:
    line = _log(production_log, fields={"message": canary, "level": canary})

    assert line["message"] == "evento"
    assert line["level"] == "WARNING"
    assert line["dropped_fields"] == 2
    assert canary not in production_log[1].getvalue()


def test_campo_nao_sobrescreve_a_contagem_de_descartes(
    production_log: ProductionLog, canary: str
) -> None:
    line = _log(production_log, fields={"dropped_fields": 0, "nome": canary})

    assert line["dropped_fields"] == 2


def test_linha_sem_descarte_nao_tem_dropped_fields(production_log: ProductionLog) -> None:
    assert "dropped_fields" not in _log(production_log, fields={"run_id": RUN})


def test_allowlist_tem_as_chaves_das_tarefas_anteriores() -> None:
    # O runbook do OpenObserve manda ler o motivo em `error_reason` e o errno em `errno`.
    assert {"error_type", "error_reason", "errno", "sqlstate", "user_id"} <= LOGGED_FIELDS
    assert "error" not in LOGGED_FIELDS


def test_sqlstate_do_registro_vence_o_do_campo(production_log: ProductionLog) -> None:
    logger, stream = production_log

    logger.warning("evento", extra={"fields": {"sqlstate": "XXXXX"}, "sqlstate": "23505"})

    assert json.loads(stream.getvalue())["sqlstate"] == "23505"
