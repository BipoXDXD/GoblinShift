"""Escolha do Runner e a trava provisória do Runner local em produção (até a fatia 4)."""

from collections.abc import Callable

import pytest

from goblinshift.entrypoints.maintenance import main as maintenance_main
from goblinshift.entrypoints.worker import RunnerNotAllowedError, build_runner
from goblinshift.entrypoints.worker import main as worker_main
from goblinshift.platform.settings import Settings
from goblinshift.runs.worker.runner import LocalRunner


def test_fora_de_producao_o_runner_local_e_o_padrao() -> None:
    assert isinstance(build_runner(Settings(environment="development")), LocalRunner)


def test_producao_recusa_runner_local_sem_confirmacao() -> None:
    with pytest.raises(RunnerNotAllowedError, match="GS_ALLOW_LOCAL_RUNNER"):
        build_runner(Settings(environment="production"))


def test_producao_aceita_runner_local_com_confirmacao_explicita() -> None:
    settings = Settings(environment="production", allow_local_runner=True)

    assert isinstance(build_runner(settings), LocalRunner)


def test_executor_ctl_ainda_nao_existe() -> None:
    with pytest.raises(RunnerNotAllowedError, match="fatia 4"):
        build_runner(Settings(runner="executor_ctl"))


@pytest.mark.parametrize("process", [worker_main, maintenance_main])
def test_processos_nao_sobem_em_producao_com_runner_local_sem_confirmacao(
    process: Callable[[], int],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("GS_ENVIRONMENT", "production")
    monkeypatch.setenv("GS_RUNNER", "local")
    monkeypatch.delenv("GS_ALLOW_LOCAL_RUNNER", raising=False)
    # configure_logging troca os handlers da raiz; o caplog volta a ser instalado depois.
    monkeypatch.setattr("goblinshift.entrypoints.worker.configure_logging", lambda: None)
    monkeypatch.setattr("goblinshift.entrypoints.maintenance.configure_logging", lambda: None)

    exit_code = process()

    assert exit_code == 2
    assert "GS_ALLOW_LOCAL_RUNNER=true" in caplog.text
