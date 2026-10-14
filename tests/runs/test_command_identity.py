"""Hash canônico do comando e formato da Idempotency-Key (docs/GoblinShiftPlan.md, 1.6)."""

import hashlib
from uuid import UUID

import pytest

from goblinshift.runs.acceptance import (
    IdempotencyKeyInvalidError,
    command_hash,
    parse_idempotency_key,
)

FIRST = UUID("01900000-0000-7000-8000-000000000001")
SECOND = UUID("01900000-0000-7000-8000-000000000002")


def test_hash_e_o_sha256_do_json_canonico_sem_espacos() -> None:
    canonical = (
        '{"input_upload_ids":["01900000-0000-7000-8000-000000000001",'
        '"01900000-0000-7000-8000-000000000002"],'
        '"parameters":{"a":[2,1],"b":{"x":"é","y":null}},'
        '"recipe_id":"csv-janitor","recipe_version":1}'
    )

    digest = command_hash(
        "csv-janitor", 1, {"b": {"y": None, "x": "é"}, "a": [2, 1]}, [FIRST, SECOND]
    )

    assert digest == hashlib.sha256(canonical.encode()).digest()


def test_ordem_das_chaves_dos_parametros_nao_muda_o_hash() -> None:
    one = command_hash("csv-janitor", 1, {"a": 1, "b": {"c": 2, "d": 3}}, [FIRST])
    other = command_hash("csv-janitor", 1, {"b": {"d": 3, "c": 2}, "a": 1}, [FIRST])

    assert one == other


def test_ordem_dos_uploads_muda_o_hash() -> None:
    one = command_hash("csv-janitor", 1, {}, [FIRST, SECOND])
    other = command_hash("csv-janitor", 1, {}, [SECOND, FIRST])

    assert one != other


def test_ordem_das_listas_dos_parametros_muda_o_hash() -> None:
    one = command_hash("csv-janitor", 1, {"a": [1, 2]}, [FIRST])
    other = command_hash("csv-janitor", 1, {"a": [2, 1]}, [FIRST])

    assert one != other


def test_receita_entra_no_hash() -> None:
    assert command_hash("csv-janitor", 1, {}, [FIRST]) != command_hash("outra", 1, {}, [FIRST])


def test_versao_entra_no_hash() -> None:
    assert command_hash("csv-janitor", 1, {}, [FIRST]) != command_hash(
        "csv-janitor", 2, {}, [FIRST]
    )


@pytest.mark.parametrize(
    "key",
    ["a" * 16, "Z" * 64, "3f0c1b6e-6a2d-4c55-9a7e-2b1f8d4c9e10", "abc_DEF-0123456789"],
)
def test_chave_no_formato_e_aceita(key: str) -> None:
    assert parse_idempotency_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        None,
        "",
        "a" * 15,
        "a" * 65,
        "a" * 15 + " ",
        "a" * 15 + "/",
        "a" * 15 + "é",
        "a" * 16 + "\n",
        "a" * 15 + "\x00",
    ],
)
def test_chave_ausente_ou_fora_do_formato_e_recusada(key: str | None) -> None:
    with pytest.raises(IdempotencyKeyInvalidError):
        parse_idempotency_key(key)
