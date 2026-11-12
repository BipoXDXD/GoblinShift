"""Entrada adulterada no storage é falha terminal, sem retry e com alerta (ADR 054).

Um symlink no lugar do objeto é evidência de adulteração, não de perda: repetir a preparação não
a desfaz e só gasta claims até `INFRA_POISON`, sem sinal algum. Entrada ausente ou ilegível por
`OSError` genérica continua transitória.
"""

import errno
import logging
import uuid
from pathlib import Path
from typing import Any, NoReturn

import pytest
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from tests.fifo_support import fail_if_blocked, make_fifo
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

INPUT_TAMPERED = "INPUT_TAMPERED"
INPUT_TAMPERED_ALERT = "input_tampered"
TAMPERED_LOG = "entrada adulterada no storage"
WORK_AREA_FAILED = "área de trabalho falhou"


def stored_object(storage: FileStorage) -> Path:
    [stored] = [path for path in storage.root.rglob("*") if path.is_file()]
    return stored


def plant_symlink_over_stored_object(storage: FileStorage, outside: Path) -> None:
    """Troca o objeto do storage por um symlink para `outside`: o `open` recusa com `ELOOP`."""
    stored = stored_object(storage)
    outside.write_bytes(b"segredo")
    stored.unlink()
    stored.symlink_to(outside)


def plant_directory_over_stored_object(storage: FileStorage) -> None:
    """Troca o objeto do storage por um diretório: o `open` recusa como adulteração."""
    stored = stored_object(storage)
    stored.unlink()
    stored.mkdir()


def plant_fifo_over_stored_object(storage: FileStorage) -> Path:
    """Troca o objeto do storage por um FIFO: o `open(O_RDONLY)` bloquearia sem escritor."""
    stored = stored_object(storage)
    stored.unlink()
    make_fifo(stored)
    return stored


def fields_of(record: logging.LogRecord) -> dict[str, Any]:
    return getattr(record, "fields")  # type: ignore[no-any-return]  # noqa: B009 - campo do `extra`


def records_named(caplog: pytest.LogCaptureFixture, message: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == message]


def run_with_tampered_input(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path, tmp_path: Path
) -> tuple[uuid.UUID, Path]:
    run_id = queue_run(factory, storage, create_user(factory))
    runner = local_runner(work_root)
    plant_symlink_over_stored_object(storage, tmp_path / "CANARY-alvo-fora-da-raiz")
    claimed = claim(factory, storage)

    applied = coordinator(factory, storage, runner).run(claimed)

    assert applied is not None
    return run_id, runner.work_dir(claimed.attempt_id)


def test_entrada_adulterada_termina_falha_na_primeira_tentativa(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path, tmp_path: Path
) -> None:
    run_id, _work_dir = run_with_tampered_input(factory, storage, work_root, tmp_path)

    row = run_row(factory, run_id)
    assert (row["status"], row["phase"], row["error_code"]) == ("failed", None, INPUT_TAMPERED)
    assert row["finished_at"] is not None
    assert open_run_uses(factory, run_id) == 0
    [only] = attempts(factory, run_id)
    assert only["ended_at"] is not None
    assert only["error_code"] == INPUT_TAMPERED


def test_entrada_adulterada_nao_consome_tentativa_fisica(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path, tmp_path: Path
) -> None:
    run_id, _work_dir = run_with_tampered_input(factory, storage, work_root, tmp_path)

    # Só o claim da própria tentativa foi gasto: o executor nunca iniciou e nenhuma outra nasce.
    assert run_row(factory, run_id)["claim_count"] == 1
    assert [row["executor_started_at"] for row in attempts(factory, run_id)] == [None]


def test_entrada_adulterada_limpa_a_area_de_trabalho(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path, tmp_path: Path
) -> None:
    _run_id, work_dir = run_with_tampered_input(factory, storage, work_root, tmp_path)

    assert not work_dir.exists()


def test_entrada_adulterada_loga_alerta_sem_o_caminho_do_disco(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    queue_run(factory, storage, create_user(factory))
    plant_symlink_over_stored_object(storage, tmp_path / "CANARY-alvo-fora-da-raiz")
    claimed = claim(factory, storage)

    with caplog.at_level(logging.WARNING):
        coordinator(factory, storage, local_runner(work_root)).run(claimed)

    [record] = records_named(caplog, TAMPERED_LOG)
    fields = fields_of(record)
    assert fields["alert"] == INPUT_TAMPERED_ALERT
    assert fields["run_id"] == str(claimed.run_id)
    assert fields["attempt_id"] == str(claimed.attempt_id)
    assert fields["position"] == 0
    everything = repr(fields) + record.getMessage()
    assert "CANARY" not in everything
    assert str(storage.root) not in everything
    assert records_named(caplog, WORK_AREA_FAILED) == []


def test_entrada_adulterada_nao_gera_o_alerta_do_executor_que_nao_iniciou(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # `gs-causa-executor-nao-iniciou` lê `alert.executor_start_failed`; a adulteração não é isso.
    queue_run(factory, storage, create_user(factory))
    plant_symlink_over_stored_object(storage, tmp_path / "CANARY-alvo-fora-da-raiz")
    claimed = claim(factory, storage)

    with caplog.at_level(logging.WARNING):
        coordinator(factory, storage, local_runner(work_root)).run(claimed)

    alerts = [
        fields_of(record).get("alert") for record in caplog.records if hasattr(record, "fields")
    ]
    assert "executor_start_failed" not in alerts


def assert_still_retried(factory: sessionmaker[Session], run_id: uuid.UUID, work_dir: Path) -> None:
    row = run_row(factory, run_id)
    assert (row["status"], row["phase"]) == ("running", "retry_wait")
    assert row["error_code"] is None
    [only] = attempts(factory, run_id)
    assert only["error_code"] == "EXECUTOR_UNAVAILABLE"
    assert not work_dir.exists()


def test_entrada_ausente_continua_reagendada(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    runner = local_runner(work_root)
    stored_object(storage).unlink()
    claimed = claim(factory, storage)

    applied = coordinator(factory, storage, runner).run(claimed)

    assert applied is not None
    assert_still_retried(factory, run_id, runner.work_dir(claimed.attempt_id))


def test_entrada_ausente_continua_sem_alerta(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    queue_run(factory, storage, create_user(factory))
    stored_object(storage).unlink()
    claimed = claim(factory, storage)

    with caplog.at_level(logging.WARNING):
        coordinator(factory, storage, local_runner(work_root)).run(claimed)

    assert records_named(caplog, TAMPERED_LOG) == []
    [record] = records_named(caplog, WORK_AREA_FAILED)
    assert "alert" not in fields_of(record)


def fail_open_with_disk_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """`FileStorage.open` falha com um erro de disco que não é adulteração (EIO)."""

    def failing_open(self: FileStorage, key: str) -> NoReturn:
        raise OSError(errno.EIO, "erro de entrada e saída")

    monkeypatch.setattr(FileStorage, "open", failing_open)


def test_oserror_generica_na_leitura_continua_reagendada(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    runner = local_runner(work_root)
    fail_open_with_disk_error(monkeypatch)
    claimed = claim(factory, storage)

    applied = coordinator(factory, storage, runner).run(claimed)

    assert applied is not None
    assert_still_retried(factory, run_id, runner.work_dir(claimed.attempt_id))


def test_oserror_generica_na_leitura_continua_sem_alerta(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue_run(factory, storage, create_user(factory))
    fail_open_with_disk_error(monkeypatch)
    claimed = claim(factory, storage)

    with caplog.at_level(logging.WARNING):
        coordinator(factory, storage, local_runner(work_root)).run(claimed)

    assert records_named(caplog, TAMPERED_LOG) == []
    [record] = records_named(caplog, WORK_AREA_FAILED)
    assert "alert" not in fields_of(record)


def test_entrada_trocada_por_diretorio_termina_falha_sem_retry(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    runner = local_runner(work_root)
    plant_directory_over_stored_object(storage)
    claimed = claim(factory, storage)

    applied = coordinator(factory, storage, runner).run(claimed)

    assert applied is not None
    row = run_row(factory, run_id)
    assert (row["status"], row["phase"], row["error_code"]) == ("failed", None, INPUT_TAMPERED)
    assert row["claim_count"] == 1
    [only] = attempts(factory, run_id)
    assert only["error_code"] == INPUT_TAMPERED
    assert not runner.work_dir(claimed.attempt_id).exists()


def test_entrada_trocada_por_diretorio_loga_alerta_sem_o_caminho_do_disco(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    queue_run(factory, storage, create_user(factory))
    plant_directory_over_stored_object(storage)
    claimed = claim(factory, storage)

    with caplog.at_level(logging.WARNING):
        coordinator(factory, storage, local_runner(work_root)).run(claimed)

    [record] = records_named(caplog, TAMPERED_LOG)
    assert fields_of(record)["alert"] == INPUT_TAMPERED_ALERT
    assert str(storage.root) not in repr(fields_of(record)) + record.getMessage()
    assert records_named(caplog, WORK_AREA_FAILED) == []


def test_entrada_trocada_por_fifo_termina_falha_sem_retry_e_sem_bloquear(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    runner = local_runner(work_root)
    fifo = plant_fifo_over_stored_object(storage)
    claimed = claim(factory, storage)

    with fail_if_blocked(fifo):
        applied = coordinator(factory, storage, runner).run(claimed)

    assert applied is not None
    row = run_row(factory, run_id)
    assert (row["status"], row["phase"], row["error_code"]) == ("failed", None, INPUT_TAMPERED)
    assert row["claim_count"] == 1
    [only] = attempts(factory, run_id)
    assert only["error_code"] == INPUT_TAMPERED
    assert not runner.work_dir(claimed.attempt_id).exists()


def test_entrada_trocada_por_fifo_loga_alerta_sem_o_caminho_do_disco(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    queue_run(factory, storage, create_user(factory))
    fifo = plant_fifo_over_stored_object(storage)
    claimed = claim(factory, storage)

    with fail_if_blocked(fifo), caplog.at_level(logging.WARNING):
        coordinator(factory, storage, local_runner(work_root)).run(claimed)

    [record] = records_named(caplog, TAMPERED_LOG)
    assert fields_of(record)["alert"] == INPUT_TAMPERED_ALERT
    assert str(storage.root) not in repr(fields_of(record)) + record.getMessage()
    assert records_named(caplog, WORK_AREA_FAILED) == []
