"""Entrada que não bate com o registro é falha transitória, sem iniciar o executor (ADR 054).

Duas recusas da preparação que o `test_input_tampered.py` não alcança:
- o objeto existe e é um arquivo regular, mas o conteúdo diverge do hash ou do tamanho registrado
  (o ADR 054 deixa essa divergência transitória de propósito: pode ser gravação interrompida);
- o upload citado pela Run já não tem objeto legível no banco (a manutenção o marcou `missing`).
"""

import logging
import uuid
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.states import ObjectState
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import stored_object
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.worker.coordinator import COPY_CHUNK_BYTES
from tests.runs.worker_support import (
    VALID,
    attempts,
    claim,
    coordinator,
    create_user,
    local_runner,
    open_run_uses,
    queue_run,
    run_row,
)

pytestmark = pytest.mark.integration

WORK_AREA_FAILED = "área de trabalho falhou"
TAMPERED_LOG = "entrada adulterada no storage"


def stored_file(storage: FileStorage) -> Path:
    [stored] = [path for path in storage.root.rglob("*") if path.is_file()]
    return stored


def assert_rescheduled_without_executor(
    factory: sessionmaker[Session], run_id: uuid.UUID, work_dir: Path
) -> None:
    row = run_row(factory, run_id)
    assert (row["status"], row["phase"]) == ("running", "retry_wait")
    assert row["error_code"] is None
    [only] = attempts(factory, run_id)
    assert only["ended_at"] is not None
    assert only["error_code"] == "EXECUTOR_UNAVAILABLE"
    assert only["executor_started_at"] is None
    assert open_run_uses(factory, run_id) == 1
    assert not work_dir.exists()


class CountingReader:
    """Objeto do storage com `total` bytes; conta quanto a cópia chegou a ler."""

    def __init__(self, total: int) -> None:
        self._remaining = total
        self.consumed = 0

    def read(self, size: int) -> bytes:
        served = min(size, self._remaining)
        self._remaining -= served
        self.consumed += served
        return b"x" * served

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


def fields_of(record: logging.LogRecord) -> dict[str, Any]:
    return getattr(record, "fields")  # type: ignore[no-any-return]  # noqa: B009 - campo do `extra`


@pytest.mark.parametrize(
    "stored_content",
    [
        pytest.param(b"x" * len(VALID), id="mesmo-tamanho-bytes-trocados"),
        pytest.param(VALID[:-3], id="truncado"),
        pytest.param(VALID + b"Bia,Natal\r\n", id="com-bytes-a-mais"),
    ],
)
def test_conteudo_que_diverge_do_registro_reagenda_sem_iniciar_o_executor(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path, stored_content: bytes
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    runner = local_runner(work_root)
    stored_file(storage).write_bytes(stored_content)
    claimed = claim(factory, storage)

    applied = coordinator(factory, storage, runner).run(claimed)

    assert applied is not None
    assert_rescheduled_without_executor(factory, run_id, runner.work_dir(claimed.attempt_id))


def test_divergencia_de_hash_loga_a_falha_da_area_sem_alerta_de_adulteracao(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    queue_run(factory, storage, create_user(factory))
    stored_file(storage).write_bytes(b"x" * len(VALID))
    claimed = claim(factory, storage)

    with caplog.at_level(logging.WARNING):
        coordinator(factory, storage, local_runner(work_root)).run(claimed)

    messages = [record.getMessage() for record in caplog.records]
    assert TAMPERED_LOG not in messages
    [record] = [r for r in caplog.records if r.getMessage() == WORK_AREA_FAILED]
    fields = fields_of(record)
    assert "alert" not in fields
    assert fields["error_type"] == "PreparationError"
    assert "error" not in fields
    assert str(storage.root) not in repr(fields)


def test_entrada_maior_que_o_registro_para_de_copiar_no_primeiro_bloco_excedente(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    reader = CountingReader(len(VALID) + 8 * COPY_CHUNK_BYTES)
    monkeypatch.setattr(storage, "open", lambda _key: reader)
    claimed = claim(factory, storage)
    runner = local_runner(work_root)

    coordinator(factory, storage, runner).run(claimed)

    assert reader.consumed <= COPY_CHUNK_BYTES
    assert_rescheduled_without_executor(factory, run_id, runner.work_dir(claimed.attempt_id))


@pytest.mark.parametrize("state", [ObjectState.MISSING, ObjectState.DELETED])
def test_entrada_sem_objeto_legivel_reagenda_sem_criar_a_area(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    state: ObjectState,
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    runner = local_runner(work_root)
    with unit_of_work(factory) as session:
        session.execute(update(stored_object).values(state=state))
    claimed = claim(factory, storage)

    applied = coordinator(factory, storage, runner).run(claimed)

    assert applied is not None
    assert_rescheduled_without_executor(factory, run_id, runner.work_dir(claimed.attempt_id))
