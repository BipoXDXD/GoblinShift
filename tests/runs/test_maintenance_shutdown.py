"""Parada da manutenção no meio da volta (triagem lote 2, L5).

A flag em si e o handler de sinal: tests/runs/test_process_shutdown.py.
"""

import signal
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

import pytest

from goblinshift.entrypoints.maintenance import AccountJobs, serve
from goblinshift.entrypoints.shutdown import ShutdownFlag
from goblinshift.platform.settings import Settings
from goblinshift.runs.maintenance import Maintenance


def test_serve_passa_a_parada_ao_ciclo_e_nao_comeca_o_seguinte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Sem banco: os ciclos são trocados, e nada abre conexão.
    shutdown = ShutdownFlag()
    seen: list[tuple[str, bool]] = []

    def executions(_self: Maintenance, should_stop: Callable[[], bool]) -> dict[str, int]:
        before = should_stop()
        shutdown.request(signal.SIGTERM)  # o sinal chega no meio da volta
        seen.append(("execuções", before))
        seen.append(("execuções depois do sinal", should_stop()))
        return {}

    def accounts(_self: AccountJobs) -> dict[str, int]:
        seen.append(("contas", True))
        shutdown.request()  # sem isto, uma regressão prenderia o teste no laço
        return {}

    monkeypatch.setattr(Maintenance, "run_cycle", executions)
    monkeypatch.setattr(AccountJobs, "run_cycle", accounts)

    serve(
        Settings(environment="test", db_port=1, storage_root=tmp_path / "storage"),
        Mock(),
        shutdown,
    )

    assert seen == [("execuções", False), ("execuções depois do sinal", True)]
