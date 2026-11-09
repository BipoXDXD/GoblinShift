"""`job.json` e a conferência das entradas: a fronteira entre o worker e o sandbox do executor."""

import json
from pathlib import Path

import pytest

from goblinshift.processing.executor.job import (
    JOB_MAX_BYTES,
    InputEntry,
    InputIntegrityError,
    Job,
    JobError,
    read_job,
    verified_inputs,
)

EMPTY_JOB = {"recipe_id": "csv-janitor", "recipe_version": 1, "parameters": {}, "inputs": []}
ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
ABC_ENTRY = InputEntry(position=0, path="a.csv", original_name="a.csv", size=3, sha256=ABC_SHA256)


def write_job(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "job.json"
    path.write_bytes(data)
    return path


def job_padded_to(size: int) -> bytes:
    """Job válido, completado com espaços (válidos em JSON), com exatamente `size` bytes."""
    body = json.dumps(EMPTY_JOB).encode()
    return body + b" " * (size - len(body))


def test_job_com_exatamente_o_teto_de_bytes_e_lido(tmp_path: Path) -> None:
    job = read_job(write_job(tmp_path, job_padded_to(JOB_MAX_BYTES)))

    assert job == Job("csv-janitor", 1, {}, ())


def test_job_um_byte_acima_do_teto_e_recusado(tmp_path: Path) -> None:
    with pytest.raises(JobError, match="job passa de 1048576 bytes"):
        read_job(write_job(tmp_path, job_padded_to(JOB_MAX_BYTES + 1)))


def test_job_ausente_e_job_error(tmp_path: Path) -> None:
    with pytest.raises(JobError, match="job ilegível"):
        read_job(tmp_path / "job.json")


def test_inputs_que_nao_e_lista_e_recusado(tmp_path: Path) -> None:
    data = json.dumps({**EMPTY_JOB, "inputs": {}}).encode()

    with pytest.raises(JobError, match="inputs deve ser uma lista"):
        read_job(write_job(tmp_path, data))


def test_entrada_com_o_sha256_declarado_e_entregue(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_bytes(b"abc")

    [verified] = verified_inputs(tmp_path, [ABC_ENTRY])

    assert (verified.position, verified.path, verified.size) == (0, tmp_path / "a.csv", 3)


def test_entrada_do_mesmo_tamanho_com_outro_conteudo_impede_a_execucao(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_bytes(b"xyz")

    with pytest.raises(InputIntegrityError, match="SHA-256 diferente do job"):
        verified_inputs(tmp_path, [ABC_ENTRY])
