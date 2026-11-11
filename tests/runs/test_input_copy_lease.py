"""Cópia da entrada para a área de trabalho sob demora (docs/GoblinShiftPlan.md, 3.4 e 3.5).

PostgreSQL e executor reais. A cópia de uma entrada grande (até 512 MiB) dura mais que o lease:
ela renova o lease entre blocos, como a coleta e a gravação dos candidatos, e é nela que um
cancelamento ou a perda do fencing é percebido.
"""

from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.runs.worker import coordinator as coordinator_module
from tests.runs.worker_support import (
    LEASE,
    SlowStorage,
    claim,
    coordinator,
    create_user,
    local_runner,
    queue_run,
    run_row,
)

pytestmark = pytest.mark.integration

HEADER = b"nome,cidade\r\n"
# Com blocos de 4 bytes cada entrada tem dezenas de blocos: sobra para 16 pausas de LEASE/8.
FIRST = HEADER + b"Ana,Recife\r\n" * 10
SECOND = HEADER + b"Bia,Olinda\r\n" * 10
PAUSES = 16
PAUSE = LEASE / 8


def test_lease_segue_renovado_durante_a_copia_da_entrada(
    factory: sessionmaker[Session],
    tmp_path: Path,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(coordinator_module, "COPY_CHUNK_BYTES", 4)
    storage = SlowStorage(tmp_path / "storage", factory)
    run_id = queue_run(
        factory, storage, create_user(factory), [("a.csv", FIRST), ("b.csv", SECOND)]
    )
    claimed = claim(factory, storage)
    storage.slow_reads(claimed.attempt_id, pauses=PAUSES, pause=PAUSE)

    applied = coordinator(factory, storage, local_runner(work_root)).run(claimed)

    assert applied is not None
    assert run_row(factory, run_id)["status"] == "succeeded"
    assert len(storage.lease_margins) == PAUSES
    # As pausas somam dois leases: sem renovar entre blocos o lease venceria no meio da cópia.
    assert PAUSES * PAUSE > LEASE
    assert min(storage.lease_margins) > 0
