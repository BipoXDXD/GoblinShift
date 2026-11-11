"""E/S que falha ao montar a área de trabalho é falha transitória na hora (3.4 e 3.5).

Sem isso a exceção sobe até o laço do worker, a Attempt fica viva até o lease vencer e a área
ocupada faz a Run seguinte receber `work_area_dirty`.
"""

import errno
import io
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


class _NoSpaceLeft(io.BytesIO):
    def write(self, _data: object, /) -> int:
        raise OSError(errno.ENOSPC, "sem espaço no dispositivo")


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
