"""Runner local: start idempotente pelo nome, status entre processos, parada do grupo e teto."""

import ast
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from goblinshift.platform.settings import LOCAL_EXECUTOR_IMAGE
from goblinshift.runs.worker import runner as runner_module
from goblinshift.runs.worker.runner import ExecutorPhase, LocalRunner, RunnerError
from tests.runs.fake_executor import IGNORING_TERM

FAKE_EXECUTOR = str(Path(__file__).with_name("fake_executor.py"))
WAIT_TIMEOUT_S = 15.0
# Quanto o "init" do teste leva para colher o zumbi: bem depois do primeiro sinal do `stop`, bem
# antes de a carência e a confirmação do SIGKILL se esgotarem.
REAP_AFTER_S = 1.0
# Bem abaixo do tempo de subida de um interpretador Python: o lançador nunca termina a tempo.
UNREACHABLE_LAUNCH_TIMEOUT_S = 0.001
SUBPROCESS_TIMEOUT_S = 60.0
# Derivados deste arquivo, não do pacote importado: o editable da .venv pode ser outro checkout.
REPO_SRC = Path(__file__).resolve().parents[2] / "src"
WORKER_SRC = REPO_SRC / "goblinshift" / "runs" / "worker"
META_FILE_NAMES = {"PGID_FILE", "EXIT_FILE"}


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


def group_gone(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        # macOS: o grupo só tem zumbis ainda não colhidos.
        return False
    return False


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


def test_lancador_que_estoura_o_prazo_vira_runner_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner_module, "LAUNCH_TIMEOUT_S", UNREACHABLE_LAUNCH_TIMEOUT_S)
    runner = fake_runner(tmp_path, "sleep", "30")

    with pytest.raises(RunnerError, match="lançador"):
        runner.start(uuid.uuid7(), LOCAL_EXECUTOR_IMAGE)


def test_lancador_que_estoura_o_prazo_nao_deixa_executor_vivo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner_module, "LAUNCH_TIMEOUT_S", UNREACHABLE_LAUNCH_TIMEOUT_S)
    launched: list[subprocess.Popen[bytes]] = []

    class RecordingPopen(subprocess.Popen[bytes]):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            launched.append(self)

    monkeypatch.setattr(subprocess, "Popen", RecordingPopen)
    runner = fake_runner(tmp_path, "sleep", "30")

    with pytest.raises(RunnerError):
        runner.start(uuid.uuid7(), LOCAL_EXECUTOR_IMAGE)

    # O lançador é líder do próprio grupo, onde também ficariam o supervisor e o executor.
    (launcher,) = launched
    assert launcher.returncode is not None
    wait_for(lambda: group_gone(launcher.pid))


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


def test_lancador_nao_importa_o_sqlalchemy() -> None:
    # A partida do lançador cabe no LAUNCH_TIMEOUT_S só se ele não arrastar runs.store (#377).
    code = (
        "import json, sys, goblinshift.runs.worker.local_supervisor as launcher\n"
        "heavy = sorted(m for m in ('sqlalchemy', 'goblinshift.runs.store') if m in sys.modules)\n"
        "print(json.dumps({'file': launcher.__file__, 'heavy': heavy}))"
    )

    result = subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=True,
        env={**os.environ, "PYTHONPATH": str(REPO_SRC)},
    )

    report = json.loads(result.stdout)
    assert Path(report["file"]).resolve().is_relative_to(WORKER_SRC)
    assert report["heavy"] == []


def assigned_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    else:
        return set()
    return {t.id for t in targets if isinstance(t, ast.Name)}


def test_nomes_dos_arquivos_de_meta_tem_uma_fonte_so() -> None:
    definers = {
        path.name
        for path in WORKER_SRC.glob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if assigned_names(node) & META_FILE_NAMES
    }

    assert definers == {"meta_files.py"}
