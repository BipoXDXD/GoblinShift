"""Leitura do header `Idempotency-Key`, comum a `POST /runs`, `/uploads` e `/api-keys` (#442)."""

import pytest
from fastapi import Request

from goblinshift.entrypoints.api.idempotency import parse_idempotency_header
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode


def request_with(headers: dict[str, str]) -> Request:
    raw = [(name.lower().encode(), value.encode()) for name, value in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": raw})


def test_sem_header_devolve_none() -> None:
    assert parse_idempotency_header(request_with({})) is None


def test_header_valido_volta_inalterado() -> None:
    key = "repeticao-do-cliente-0001"

    assert parse_idempotency_header(request_with({"Idempotency-Key": key})) == key


@pytest.mark.parametrize("raw", ["curta", "x" * 65, "com espaço-0000000", ""])
def test_header_malformado_vira_problema_de_chave_invalida(raw: str) -> None:
    with pytest.raises(ApiProblem) as raised:
        parse_idempotency_header(request_with({"Idempotency-Key": raw}))

    assert raised.value.code is ProblemCode.IDEMPOTENCY_KEY_INVALID
