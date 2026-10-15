"""A manutenção reconcilia o livro-razão de capacidade a cada ciclo (plano da fatia 6, 3.3)."""

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.capacity import HolderKind, release
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import upload
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.lifecycle import MIN_RETRY_JITTER
from goblinshift.runs.maintenance import Maintenance, MaintenanceConfig
from tests.capacity.support import account_reserved, live_amount
from tests.files.support import create_user, ready_upload
from tests.runs.worker_support import local_runner

pytestmark = pytest.mark.integration


def test_ciclo_adota_upload_do_deploy_anterior_e_conta_no_evento(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    owner = create_user(factory, "alice")
    view = ready_upload(factory, storage, owner, b"abcdef")
    with factory() as session:
        object_id = session.execute(
            select(upload.c.object_id).where(upload.c.id == view.id)
        ).scalar_one()
    with unit_of_work(factory) as session:
        release(session, holder=HolderKind.OBJECT, holder_id=object_id)
    config = MaintenanceConfig(stop_grace=timedelta(seconds=2), jitter=lambda: MIN_RETRY_JITTER)

    counts = Maintenance(factory, storage, local_runner(work_root), config).run_cycle()

    assert counts["capacity_objects_adopted"] == 1
    assert counts["capacity_pools_corrected"] == 0
    assert live_amount(factory, HolderKind.OBJECT, object_id) == 6
    assert account_reserved(factory, owner) == 6
