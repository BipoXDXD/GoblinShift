"""FIFO no lugar da saída coletada, na hora de copiá-la para o storage (TOCTOU).

A coleta confere o tipo de cada arquivo de out/ e o devolve em `Collected`; o `CandidateWriter`
só o abre depois, para copiar. O sandbox ainda pode trocá-lo por um FIFO nessa janela: um
`open(O_RDONLY)` sem `O_NONBLOCK` bloquearia a thread do worker. O vigia reprova o teste em vez
de travar a suíte. O erro segue o de qualquer falha ao ler a saída: transitório, com retentativa.

O vigia liga quando o FIFO é plantado, não antes de `run`: o executor real em subprocesso leva
segundos sob carga, e esse custo não diz nada sobre bloquear no FIFO.
"""

import os
import stat
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.runs.worker import collection
from goblinshift.runs.worker import coordinator as coordinator_module
from goblinshift.runs.worker.collection import Collected
from tests.fifo_support import BlockWatchdog, make_fifo
from tests.runs.worker_support import (
    VALID,
    attempts,
    claim,
    coordinator,
    create_user,
    local_runner,
    queue_run,
    run_row,
)

pytestmark = pytest.mark.integration

UTF8_BOM = b"\xef\xbb\xbf"
# Com BOM a saída limpa difere da entrada: vira candidato gravado, não reaproveitamento.
NEEDS_WRITE = UTF8_BOM + VALID


def _swap_first_output_for_fifo_after_collect(
    monkeypatch: pytest.MonkeyPatch, planted: list[Path], watchdog: BlockWatchdog
) -> None:
    """Depois que a coleta devolveu `Collected`, o sandbox troca a primeira saída por um FIFO.

    O vigia começa a contar no plantio: daí em diante só resta copiar e gravar no banco.
    """

    def collect_then_swap(*args: Any, **kwargs: Any) -> Collected:
        collected = collection.collect_outputs(*args, **kwargs)
        path = collected.outputs[0].path
        path.unlink()
        make_fifo(path)
        planted.append(path)
        watchdog.start()
        return collected

    monkeypatch.setattr(f"{coordinator_module.__name__}.collect_outputs", collect_then_swap)


def _record_opened_fds(monkeypatch: pytest.MonkeyPatch, path: Path) -> list[int]:
    """Descritores que o código abriu para `path`, para conferir depois que foram fechados."""
    opened: list[int] = []
    real_open = os.open

    def spying_open(target: Any, *args: Any, **kwargs: Any) -> int:
        fd = real_open(target, *args, **kwargs)
        if str(target) == str(path):
            opened.append(fd)
        return fd

    monkeypatch.setattr(os, "open", spying_open)
    return opened


def _still_open_fifos(fds: list[int]) -> list[int]:
    leaked = []
    for fd in fds:
        try:
            if stat.S_ISFIFO(os.fstat(fd).st_mode):
                leaked.append(fd)
        except OSError:
            continue  # fechado: é o esperado
    return leaked


def test_saida_trocada_por_fifo_antes_da_copia_e_falha_transitoria_sem_bloquear(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = queue_run(factory, storage, create_user(factory), [("a.csv", NEEDS_WRITE)])
    claimed = claim(factory, storage)
    runner = local_runner(work_root)
    out_file = runner.work_dir(claimed.attempt_id) / "out" / "a.csv"
    opened = _record_opened_fds(monkeypatch, out_file)
    planted: list[Path] = []
    watchdog = BlockWatchdog(out_file)
    _swap_first_output_for_fifo_after_collect(monkeypatch, planted, watchdog)

    try:
        applied = coordinator(factory, storage, runner).run(claimed)
    finally:
        watchdog.halt()
    watchdog.check()

    assert applied is not None
    assert planted == [out_file]
    row = run_row(factory, run_id)
    assert (row["status"], row["phase"]) == ("running", "retry_wait")
    [only] = attempts(factory, run_id)
    assert only["error_code"] == "EXECUTOR_UNAVAILABLE"
    assert opened
    assert _still_open_fifos(opened) == []
