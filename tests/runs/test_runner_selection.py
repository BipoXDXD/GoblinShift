"""Escolha do Runner, imagem do executor e a trava de produção (docs/GoblinShiftPlan.md, 3.9)."""

import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from goblinshift.entrypoints.maintenance import main as maintenance_main
from goblinshift.entrypoints.worker import build_runner
from goblinshift.entrypoints.worker import main as worker_main
from goblinshift.platform.settings import LOCAL_EXECUTOR_IMAGE, ExecutorConfigError, Settings
from goblinshift.runs.worker.executor_ctl_runner import ExecutorCtlRunner
from goblinshift.runs.worker.runner import LocalRunner

IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "a" * 64


def production(**values: object) -> Settings:
    return Settings.model_validate({"environment": "production", **values})


@pytest.mark.parametrize("environment", ["development", "test"])
def test_fora_de_producao_o_runner_local_e_o_padrao(environment: str) -> None:
    settings = Settings.model_validate({"environment": environment})

    assert isinstance(build_runner(settings), LocalRunner)
    assert settings.executor_image_for_runs() == LOCAL_EXECUTOR_IMAGE == "local-dev"


def test_producao_recusa_o_runner_local_sem_excecao() -> None:
    with pytest.raises(ExecutorConfigError, match="GS_RUNNER=executor_ctl"):
        build_runner(production(runner="local"))


def test_producao_aceita_o_executor_ctl_com_imagem() -> None:
    settings = production(runner="executor_ctl", executor_image=IMAGE, work_root=Path("/w"))

    attempt_id = uuid.uuid7()

    runner = build_runner(settings)

    assert isinstance(runner, ExecutorCtlRunner)
    assert runner.work_dir(attempt_id) == Path("/w") / str(attempt_id)
    assert settings.executor_image_for_runs() == IMAGE


@pytest.mark.parametrize("environment", ["development", "production"])
def test_executor_ctl_sem_imagem_nao_sobe(environment: str) -> None:
    settings = Settings.model_validate({"environment": environment, "runner": "executor_ctl"})

    with pytest.raises(ExecutorConfigError, match="GS_EXECUTOR_IMAGE"):
        build_runner(settings)


@pytest.mark.parametrize(
    "image",
    [
        "ghcr.io/bipoxdxd/goblinshift-executor:sha-0123456789ab",
        "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "a" * 63,
        "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "A" * 64,
        "@sha256:" + "a" * 64,
        "local-dev",
    ],
)
def test_imagem_sem_digest_completo_e_recusada(image: str) -> None:
    with pytest.raises(ValidationError):
        Settings(executor_image=image)


def test_a_configuracao_nao_tem_mais_a_confirmacao_do_runner_local() -> None:
    assert "allow_local_runner" not in Settings.model_fields


@pytest.mark.parametrize("process", [worker_main, maintenance_main])
def test_processos_nao_sobem_em_producao_com_runner_local(
    process: Callable[[], int],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("GS_ENVIRONMENT", "production")
    monkeypatch.setenv("GS_RUNNER", "local")
    # A confirmação antiga não reabre a porta: a variável é ignorada.
    monkeypatch.setenv("GS_ALLOW_LOCAL_RUNNER", "true")
    # configure_logging troca os handlers da raiz; o caplog volta a ser instalado depois.
    monkeypatch.setattr("goblinshift.entrypoints.worker.configure_logging", lambda: None)
    monkeypatch.setattr("goblinshift.entrypoints.maintenance.configure_logging", lambda: None)

    exit_code = process()

    assert exit_code == 2
    assert "GS_RUNNER=executor_ctl" in caplog.text


@pytest.mark.parametrize("process", [worker_main, maintenance_main])
def test_processos_nao_sobem_em_producao_sem_o_segredo_do_user_hash(
    process: Callable[[], int],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # O evento de término da Run (3.7) leva o `user.hash`: sem a chave, nada sobe.
    monkeypatch.setenv("GS_ENVIRONMENT", "production")
    monkeypatch.setenv("GS_RUNNER", "executor_ctl")
    monkeypatch.setenv("GS_EXECUTOR_IMAGE", IMAGE)
    monkeypatch.delenv("GS_USER_HASH_SECRET", raising=False)
    monkeypatch.setattr("goblinshift.entrypoints.worker.configure_logging", lambda: None)
    monkeypatch.setattr("goblinshift.entrypoints.maintenance.configure_logging", lambda: None)

    exit_code = process()

    assert exit_code == 2
    assert "GS_USER_HASH_SECRET" in caplog.text
