"""Leitura do `failure.json` sem banco: o documento vira um código do catálogo ou nada (3.6)."""

import json
import os
import threading
from pathlib import Path

import pytest

from goblinshift.runs.lifecycle import ErrorCode
from goblinshift.runs.worker.coordinator import FAILURE_MAX_BYTES, read_failure_code

READ_TIMEOUT_SECONDS = 2


def write(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "failure.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_codigo_do_catalogo_e_lido(tmp_path: Path) -> None:
    path = write(tmp_path, {"code": "OUTPUT_LIMIT_EXCEEDED", "detail": "acima do limite"})

    assert read_failure_code(path) is ErrorCode.OUTPUT_LIMIT_EXCEEDED


def test_arquivo_no_teto_e_lido(tmp_path: Path) -> None:
    document = json.dumps({"code": "OUTPUT_LIMIT_EXCEEDED"})
    path = tmp_path / "failure.json"
    path.write_text(document.ljust(FAILURE_MAX_BYTES), encoding="utf-8")

    assert read_failure_code(path) is ErrorCode.OUTPUT_LIMIT_EXCEEDED


def test_executor_nao_declara_entrada_adulterada(tmp_path: Path) -> None:
    # Só o worker, que lê o storage, constata a adulteração; o executor é sandbox da receita.
    path = write(tmp_path, {"code": "INPUT_TAMPERED"})

    assert read_failure_code(path) is None


def test_arquivo_ausente_nao_tem_codigo(tmp_path: Path) -> None:
    assert read_failure_code(tmp_path / "failure.json") is None


def test_arquivo_acima_do_teto_nao_tem_codigo(tmp_path: Path) -> None:
    document = json.dumps({"code": "OUTPUT_LIMIT_EXCEEDED"})
    path = tmp_path / "failure.json"
    path.write_text(document.ljust(FAILURE_MAX_BYTES + 1), encoding="utf-8")

    assert read_failure_code(path) is None


def test_fifo_nao_tem_codigo(tmp_path: Path) -> None:
    path = tmp_path / "failure.json"
    os.mkfifo(path)
    results: list[ErrorCode | None] = []
    reader = threading.Thread(target=lambda: results.append(read_failure_code(path)), daemon=True)

    reader.start()
    reader.join(READ_TIMEOUT_SECONDS)
    finished = not reader.is_alive()
    if not finished:
        os.close(os.open(path, os.O_WRONLY | os.O_NONBLOCK))

    assert finished
    assert results == [None]


def test_diretorio_nao_tem_codigo(tmp_path: Path) -> None:
    path = tmp_path / "failure.json"
    path.mkdir()

    assert read_failure_code(path) is None


def test_symlink_nao_e_seguido(tmp_path: Path) -> None:
    target = tmp_path / "alvo.json"
    target.write_text(json.dumps({"code": "OUTPUT_LIMIT_EXCEEDED"}), encoding="utf-8")
    link = tmp_path / "failure.json"
    link.symlink_to(target)

    assert read_failure_code(link) is None


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"{",
        b"\xff\xfe\x00",
        b'{"code": "OUTPUT_LIMIT_EXCEEDED", "code": "OUTPUT_LIMIT_EXCEEDED"}',
        b'{"code": NaN}',
    ],
    ids=["vazio", "truncado", "bytes-invalidos", "chave-repetida", "nan"],
)
def test_json_invalido_nao_tem_codigo(tmp_path: Path, data: bytes) -> None:
    path = tmp_path / "failure.json"
    path.write_bytes(data)

    assert read_failure_code(path) is None


@pytest.mark.parametrize(
    "document",
    [["OUTPUT_LIMIT_EXCEEDED"], "OUTPUT_LIMIT_EXCEEDED", 1, None],
    ids=["lista", "string", "numero", "null"],
)
def test_documento_que_nao_e_objeto_nao_tem_codigo(tmp_path: Path, document: object) -> None:
    assert read_failure_code(write(tmp_path, document)) is None


@pytest.mark.parametrize(
    "code",
    [None, 1, 1.5, True, ["OUTPUT_LIMIT_EXCEEDED"], {"code": "OUTPUT_LIMIT_EXCEEDED"}],
    ids=["null", "inteiro", "decimal", "booleano", "lista", "objeto"],
)
def test_code_que_nao_e_string_nao_tem_codigo(tmp_path: Path, code: object) -> None:
    assert read_failure_code(write(tmp_path, {"code": code})) is None


def test_documento_sem_code_nao_tem_codigo(tmp_path: Path) -> None:
    assert read_failure_code(write(tmp_path, {"detail": "sem código"})) is None


@pytest.mark.parametrize("code", ["EXECUTOR_UNAVAILABLE", "CODIGO_DESCONHECIDO", ""], ids=str)
def test_code_fora_do_catalogo_nao_tem_codigo(tmp_path: Path, code: str) -> None:
    assert read_failure_code(write(tmp_path, {"code": code})) is None
