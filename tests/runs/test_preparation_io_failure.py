"""E/S que falha ao montar a área de trabalho é falha transitória na hora (3.4 e 3.5).

Sem isso a exceção sobe até o laço do worker, a Attempt fica viva até o lease vencer e a área
ocupada faz a Run seguinte receber `work_area_dirty`.
"""

import errno
import io
import logging
import uuid
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO, cast

import pytest
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.runs.worker import coordinator as coordinator_module
from goblinshift.runs.worker.runner import LocalRunner
from tests.runs.worker_support import (
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
EXECUTOR_START_FAILED = "executor_start_failed"


class _NoSpaceLeft(io.BytesIO):
    def write(self, _data: object, /) -> int:
        raise OSError(errno.ENOSPC, "sem espaço no dispositivo")


class _NoSpaceOnClose(io.BytesIO):
    """O buffer só chega ao disco no fechamento, e é nele que o disco cheio aparece."""

    def close(self) -> None:
        super().close()
        raise OSError(errno.ENOSPC, "sem espaço no dispositivo")


class _ReadFailsMidCopy(io.BytesIO):
    """Objeto do storage cuja leitura falha com `EIO` depois do primeiro bloco."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self._reads = 0

    def read(self, size: int | None = -1, /) -> bytes:
        self._reads += 1
        if self._reads > 1:
            raise OSError(errno.EIO, "erro de E/S")
        return super().read(size)


def fail_storage_read_mid_copy(storage: FileStorage, monkeypatch: pytest.MonkeyPatch) -> None:
    """Faz `storage.open` abrir bem e falhar no `read`: o `open` de verdade não chega lá."""
    real_open = storage.open

    def open_then_fail(key: str) -> BinaryIO:
        with real_open(key) as source:
            return cast(BinaryIO, _ReadFailsMidCopy(source.read()))

    monkeypatch.setattr(storage, "open", open_then_fail)


def work_area_failures(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == WORK_AREA_FAILED]


def fields_of(record: logging.LogRecord) -> dict[str, object]:
    return getattr(record, "fields")  # type: ignore[no-any-return]  # noqa: B009 - campo do `extra`


def alert_of(record: logging.LogRecord) -> object:
    return fields_of(record).get("alert")


def assert_rescheduled_without_executor(
    factory: sessionmaker[Session],
    run_id: uuid.UUID,
    work_dir: Path,
) -> None:
    row = run_row(factory, run_id)
    assert (row["status"], row["phase"]) == ("running", "retry_wait")
    [only] = attempts(factory, run_id)
    assert only["ended_at"] is not None
    assert only["error_code"] == "EXECUTOR_UNAVAILABLE"
    assert only["executor_started_at"] is None
    assert open_run_uses(factory, run_id) == 1
    assert not work_dir.exists()


@pytest.mark.parametrize("file_name", ["0000.bin", "job.json", "context.json"])
def test_disco_cheio_ao_gravar_na_area_reagenda_na_hora(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    file_name: str,
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    runner = local_runner(work_root)
    real_create = coordinator_module._create_for_executor

    def create(path: Path) -> BinaryIO:
        if path.name == file_name:
            return cast(BinaryIO, _NoSpaceLeft())
        return real_create(path)

    monkeypatch.setattr(coordinator_module, "_create_for_executor", create)
    claimed = claim(factory, storage)

    applied = coordinator(factory, storage, runner).run(claimed)

    assert applied is not None
    assert_rescheduled_without_executor(factory, run_id, runner.work_dir(claimed.attempt_id))


@pytest.mark.parametrize("file_name", ["0000.bin", "job.json", "context.json"])
@pytest.mark.parametrize("failure", [_NoSpaceLeft, _NoSpaceOnClose])
def test_disco_cheio_ao_gravar_na_area_alerta_executor_start_failed(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    file_name: str,
    failure: type[io.BytesIO],
) -> None:
    queue_run(factory, storage, create_user(factory))
    real_create = coordinator_module._create_for_executor

    def create(path: Path) -> BinaryIO:
        if path.name == file_name:
            return cast(BinaryIO, failure())
        return real_create(path)

    monkeypatch.setattr(coordinator_module, "_create_for_executor", create)
    claimed = claim(factory, storage)

    with caplog.at_level(logging.WARNING):
        coordinator(factory, storage, local_runner(work_root)).run(claimed)

    [record] = work_area_failures(caplog)
    assert alert_of(record) == EXECUTOR_START_FAILED


@pytest.mark.parametrize("file_name", ["0000.bin", "job.json", "context.json"])
def test_falha_ao_criar_arquivo_na_area_alerta_executor_start_failed(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    file_name: str,
) -> None:
    queue_run(factory, storage, create_user(factory))
    real_create = coordinator_module._create_for_executor

    def create(path: Path) -> BinaryIO:
        if path.name == file_name:
            raise OSError(errno.EIO, "erro de E/S")
        return real_create(path)

    monkeypatch.setattr(coordinator_module, "_create_for_executor", create)
    claimed = claim(factory, storage)

    with caplog.at_level(logging.WARNING):
        coordinator(factory, storage, local_runner(work_root)).run(claimed)

    [record] = work_area_failures(caplog)
    assert alert_of(record) == EXECUTOR_START_FAILED


def test_erro_de_leitura_do_storage_reagenda_na_hora(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    runner = local_runner(work_root)
    [stored] = [path for path in storage.root.rglob("*") if path.is_file()]
    stored.unlink()
    # `open` num diretório levanta `IsADirectoryError` de verdade, mesmo rodando como root.
    stored.mkdir()
    claimed = claim(factory, storage)

    applied = coordinator(factory, storage, runner).run(claimed)

    assert applied is not None
    assert_rescheduled_without_executor(factory, run_id, runner.work_dir(claimed.attempt_id))


def test_erro_de_leitura_do_storage_falha_sem_alerta(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    queue_run(factory, storage, create_user(factory))
    [stored] = [path for path in storage.root.rglob("*") if path.is_file()]
    stored.unlink()
    stored.mkdir()
    claimed = claim(factory, storage)

    with caplog.at_level(logging.WARNING):
        coordinator(factory, storage, local_runner(work_root)).run(claimed)

    [record] = work_area_failures(caplog)
    assert alert_of(record) is None


def test_erro_na_leitura_do_objeto_no_meio_da_copia_reagenda_na_hora(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(coordinator_module, "COPY_CHUNK_BYTES", 4)
    run_id = queue_run(factory, storage, create_user(factory))
    runner = local_runner(work_root)
    fail_storage_read_mid_copy(storage, monkeypatch)
    claimed = claim(factory, storage)

    applied = coordinator(factory, storage, runner).run(claimed)

    assert applied is not None
    assert_rescheduled_without_executor(factory, run_id, runner.work_dir(claimed.attempt_id))


def test_erro_na_leitura_do_objeto_no_meio_da_copia_falha_sem_alerta(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(coordinator_module, "COPY_CHUNK_BYTES", 4)
    queue_run(factory, storage, create_user(factory))
    fail_storage_read_mid_copy(storage, monkeypatch)
    claimed = claim(factory, storage)

    with caplog.at_level(logging.WARNING):
        coordinator(factory, storage, local_runner(work_root)).run(claimed)

    [record] = work_area_failures(caplog)
    assert alert_of(record) is None
    assert "erro de E/S" in str(fields_of(record)["error"])


class _WorkAreaRefused(LocalRunner):
    def create_work_dir(self, attempt_id: uuid.UUID) -> Path:
        raise PermissionError(errno.EACCES, "sem permissão", str(self.work_dir(attempt_id)))


def test_falha_ao_criar_a_area_reagenda_na_hora(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    runner = _WorkAreaRefused(work_root, attempt_timeout=timedelta(seconds=30))
    claimed = claim(factory, storage)

    applied = coordinator(factory, storage, runner).run(claimed)

    assert applied is not None
    assert_rescheduled_without_executor(factory, run_id, runner.work_dir(claimed.attempt_id))


def test_defeito_de_programacao_na_preparacao_nao_vira_falha_transitoria(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))

    def create(_path: Path) -> BinaryIO:
        raise TypeError("defeito")

    monkeypatch.setattr(coordinator_module, "_create_for_executor", create)
    claimed = claim(factory, storage)

    with pytest.raises(TypeError):
        coordinator(factory, storage, local_runner(work_root)).run(claimed)

    assert attempts(factory, run_id)[0]["ended_at"] is None
