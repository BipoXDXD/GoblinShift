"""Saída reescrita ou trocada depois da coleta, antes da cópia para o storage (TOCTOU, #442).

A coleta devolve cada arquivo de out/ com o inode que a varredura viu e o SHA-256 que o worker
calculou; o manifesto e o pacote foram conferidos contra esses hashes. O `CandidateWriter` só
copia depois, e nessa janela o sandbox ainda pode reescrever a saída no mesmo inode ou trocá-la
por outro arquivo. A cópia reabre pelo mesmo caminho seguro da coleta (inode conferido) e compara
o SHA-256 e o tamanho gravados com os coletados: divergência é falha transitória, sem candidato
confirmado.
"""

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import stored_object
from goblinshift.runs.worker import collection
from goblinshift.runs.worker import coordinator as coordinator_module
from goblinshift.runs.worker.collection import Collected, OutputFile
from tests.runs.worker_support import (
    VALID,
    attempts,
    claim,
    coordinator,
    create_user,
    local_runner,
    queue_run,
    record_finish,
    run_row,
    why_not_collected,
)

pytestmark = pytest.mark.integration

UTF8_BOM = b"\xef\xbb\xbf"
# Com BOM a saída limpa difere da entrada: vira candidato gravado, não reaproveitamento.
NEEDS_WRITE = UTF8_BOM + VALID


def _after_collect(
    monkeypatch: pytest.MonkeyPatch, pick: Callable[[Collected], OutputFile | None], tamper: Any
) -> list[Path]:
    """Depois que a coleta devolveu `Collected`, o sandbox mexe no arquivo que `pick` escolhe.

    Devolve a lista dos caminhos adulterados, para o teste conferir que a coleta aconteceu.
    """
    tampered: list[Path] = []

    def collect_then_tamper(*args: Any, **kwargs: Any) -> Collected:
        collected = collection.collect_outputs(*args, **kwargs)
        output = pick(collected)
        assert output is not None
        tamper(output.path, output.size)
        tampered.append(output.path)
        return collected

    monkeypatch.setattr(f"{coordinator_module.__name__}.collect_outputs", collect_then_tamper)
    return tampered


def _rewrite_in_place(path: Path, size: int) -> None:
    """Mesmo inode, mesmo tamanho, outros bytes: só o hash da cópia pega."""
    with path.open("r+b") as target:
        target.write(b"X" * size)


def _replace_with_other_file(path: Path, size: int) -> None:
    """Outro arquivo, do mesmo tamanho, no lugar do coletado."""
    path.unlink()
    path.write_bytes(b"Z" * size)


def committed_objects_of_attempt(factory: sessionmaker[Session], attempt_id: uuid.UUID) -> int:
    with factory() as session:
        return session.execute(
            select(func.count())
            .select_from(stored_object)
            .where(stored_object.c.attempt_id == attempt_id, stored_object.c.state == "committed")
        ).scalar_one()


def _run_and_expect_retry(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    pick: Callable[[Collected], OutputFile | None],
    tamper: Any,
) -> None:
    run_id = queue_run(factory, storage, create_user(factory), [("a.csv", NEEDS_WRITE)])
    claimed = claim(factory, storage)
    tampered = _after_collect(monkeypatch, pick, tamper)
    worker = coordinator(factory, storage, local_runner(work_root))
    finished = record_finish(monkeypatch, worker)

    applied = worker.run(claimed)

    assert applied is not None
    # Sob load alto o executor real pode estourar o teto da tentativa antes da coleta.
    assert len(tampered) == 1, why_not_collected(factory, run_id, finished)
    row = run_row(factory, run_id)
    assert (row["status"], row["phase"]) == ("running", "retry_wait")
    [only] = attempts(factory, run_id)
    assert only["error_code"] == "EXECUTOR_UNAVAILABLE"
    assert committed_objects_of_attempt(factory, claimed.attempt_id) == 0


def test_saida_reescrita_no_mesmo_inode_depois_da_coleta_e_falha_transitoria(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _run_and_expect_retry(
        factory,
        storage,
        work_root,
        monkeypatch,
        lambda collected: collected.outputs[0],
        _rewrite_in_place,
    )


def test_pacote_trocado_depois_da_coleta_nao_vira_candidato(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _run_and_expect_retry(
        factory,
        storage,
        work_root,
        monkeypatch,
        lambda collected: collected.package_file,
        _replace_with_other_file,
    )
