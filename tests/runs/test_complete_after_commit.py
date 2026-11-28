"""Ordem do que `complete_after_commit` faz depois do commit da transição (#442, 17b)."""

import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.runs import store
from goblinshift.runs.lifecycle import RunState
from goblinshift.runs.store import Applied, complete_after_commit


class _NoSession:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *_: object) -> None:
        return None


def test_falha_do_banco_na_limpeza_da_outbox_nao_prende_os_usos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def released(_factory: object, _work: Callable[[Session], object]) -> None:
        calls.append("release_run_holds")

    def outbox_down(*_args: object) -> tuple[str, ...]:
        calls.append("remove_files_after_commit")
        raise OperationalError("DELETE FROM file_removal", {}, Exception("banco caiu"))

    monkeypatch.setattr(store, "run_transaction", released)
    monkeypatch.setattr(store, "remove_files_after_commit", outbox_down)
    applied = Applied(
        run_id=uuid.uuid4(),
        state=cast(RunState, SimpleNamespace(generation=1)),
        started_attempt_id=None,
        stop_requested=False,
        release_pending=True,
        discarded_keys=("runs/r/g1/saida",),
    )

    with pytest.raises(OperationalError):
        complete_after_commit(
            cast(sessionmaker[Session], _NoSession),
            cast(FileStorage, None),
            applied,
        )

    assert calls == ["release_run_holds", "remove_files_after_commit"]
