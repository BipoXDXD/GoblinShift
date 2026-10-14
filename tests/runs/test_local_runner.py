"""Runner local: start idempotente pelo nome, status entre processos, parada do grupo e teto."""

import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest

from goblinshift.platform.settings import LOCAL_EXECUTOR_IMAGE
from goblinshift.runs.worker.runner import ExecutorPhase, LocalRunner, RunnerError
from tests.runs.fake_executor import IGNORING_TERM

FAKE_EXECUTOR = str(Path(__file__).with_name("fake_executor.py"))
WAIT_TIMEOUT_S = 15.0
# Quanto o "init" do teste leva para colher o zumbi: bem depois do primeiro sinal do `stop`, bem
# antes de a carência e a confirmação do SIGKILL se esgotarem.
REAP_AFTER_S = 1.0


def fake_runner(
    work_root: Path, *mode: str, timeout: timedelta = timedelta(minutes=1)
) -> LocalRunner:
    return LocalRunner(
        work_root, attempt_timeout=timeout, command=(sys.executable, FAKE_EXECUTOR, *mode)
    )


def wait_for(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + WAIT_TIMEOUT_S
    while not condition():
        assert time.monotonic() < deadline, "condição não chegou a tempo"
        time.sleep(0.05)


def wait_exit(runner: LocalRunner, attempt_id: uuid.UUID) -> None:
    wait_for(lambda: not runner.status(attempt_id).is_running)


def test_status_de_tentativa_nunca_iniciada_e_absent(tmp_path: Path) -> None:
    runner = fake_runner(tmp_path, "exit", "0")

    status = runner.status(uuid.uuid7())

    assert status.phase is ExecutorPhase.ABSENT
    assert not status.ran


def test_executor_que_termina_relata_o_codigo_de_saida(tmp_path: Path) -> None:
    runner = fake_runner(tmp_path, "exit", "3")
    attempt_id = uuid.uuid7()

    runner.start(attempt_id, LOCAL_EXECUTOR_IMAGE)
    wait_exit(runner, attempt_id)

    status = runner.status(attempt_id)
    assert status.phase is ExecutorPhase.EXITED
    assert status.exit_code == 3
    assert not status.timed_out


def test_start_repetido_adota_o_executor_existente(tmp_path: Path) -> None:
    runner = fake_runner(tmp_path, "ignore-term", "30")
    attempt_id = uuid.uuid7()
    runner.start(attempt_id, LOCAL_EXECUTOR_IMAGE)
    pgid_before = (tmp_path / f"gs-exec-{attempt_id}.runner" / "pgid").read_text()

    status = runner.start(attempt_id, LOCAL_EXECUTOR_IMAGE)

    assert status.is_running
    assert (tmp_path / f"gs-exec-{attempt_id}.runner" / "pgid").read_text() == pgid_before
    runner.stop(attempt_id, timedelta(seconds=0))


def test_stop_de_grupo_que_so_tem_zumbis_espera_a_colheita_sem_falhar(tmp_path: Path) -> None:
    # Morto pelo SIGTERM, o grupo do executor fica só com zumbis até o init colhê-los. Nessa
    # janela o macOS responde EPERM a qualquer killpg, inclusive ao SIGKILL do fim da carência.
    runner = fake_runner(tmp_path, "exit", "0")
    attempt_id = uuid.uuid7()
    zombie = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    os.waitid(os.P_PID, zombie.pid, os.WEXITED | os.WNOWAIT)  # saiu, ainda sem ser colhido
    meta = tmp_path / f"gs-exec-{attempt_id}.runner"
    meta.mkdir()
    (meta / "pgid").write_text(str(zombie.pid), encoding="ascii")
    init = threading.Timer(REAP_AFTER_S, zombie.wait)
    init.start()
    try:
        status = runner.stop(attempt_id, timedelta(seconds=2))
    finally:
        init.join()

    assert status.phase is ExecutorPhase.EXITED


def test_outro_runner_enxerga_o_executor_pelo_nome(tmp_path: Path) -> None:
    attempt_id = uuid.uuid7()
    fake_runner(tmp_path, "sleep", "30").start(attempt_id, LOCAL_EXECUTOR_IMAGE)
    # Outra instância, como a da manutenção em outro processo.
    other = fake_runner(tmp_path, "exit", "0")

    assert other.status(attempt_id).is_running
    stopped = other.stop(attempt_id, timedelta(seconds=5))

    assert stopped.phase is ExecutorPhase.EXITED
    assert stopped.exit_code is None


def test_stop_mata_com_sigkill_quem_ignora_sigterm_depois_da_carencia(tmp_path: Path) -> None:
    runner = fake_runner(tmp_path, "ignore-term", "30")
    attempt_id = uuid.uuid7()
    runner.start(attempt_id, LOCAL_EXECUTOR_IMAGE)
    executor_log = tmp_path / f"gs-exec-{attempt_id}.runner" / "executor.log"
    wait_for(lambda: IGNORING_TERM in executor_log.read_text(encoding="utf-8"))

    started = time.monotonic()
    status = runner.stop(attempt_id, timedelta(seconds=1))

    assert status.phase is ExecutorPhase.EXITED
    assert 1.0 <= time.monotonic() - started < 5.0


def test_teto_da_tentativa_encerra_o_executor(tmp_path: Path) -> None:
    runner = fake_runner(tmp_path, "sleep", "30", timeout=timedelta(seconds=0.5))
    attempt_id = uuid.uuid7()

    runner.start(attempt_id, LOCAL_EXECUTOR_IMAGE)
    wait_exit(runner, attempt_id)

    status = runner.status(attempt_id)
    assert status.timed_out
    assert status.phase is ExecutorPhase.EXITED


def test_remove_recusa_executor_vivo_e_apaga_a_area_depois_de_parado(tmp_path: Path) -> None:
    runner = fake_runner(tmp_path, "sleep", "30")
    attempt_id = uuid.uuid7()
    runner.work_dir(attempt_id).mkdir()
    runner.start(attempt_id, LOCAL_EXECUTOR_IMAGE)

    with pytest.raises(RunnerError):
        runner.remove(attempt_id)
    runner.stop(attempt_id, timedelta(seconds=5))
    runner.remove(attempt_id)

    assert list(tmp_path.iterdir()) == []
    assert runner.status(attempt_id).phase is ExecutorPhase.ABSENT
