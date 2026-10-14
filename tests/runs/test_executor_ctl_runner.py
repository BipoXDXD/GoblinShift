"""Cliente do `executor-ctl` contra o serviço falso fiel ao contrato da fatia 4."""

import os
import stat
import sys
import time
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from goblinshift.runs.worker.executor_ctl_runner import ExecutorCtlError, ExecutorCtlRunner
from goblinshift.runs.worker.runner import ExecutorPhase
from tests.runs.fake_executor_ctl import IMAGE, FakeContainer, FakeExecutorCtl

FAKE_EXECUTOR = str(Path(__file__).with_name("fake_executor.py"))
SHORT_TIMEOUT = timedelta(seconds=0.3)
WAIT_TIMEOUT_S = 15.0
OTHER_IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "b" * 64


@pytest.fixture
def area(tmp_path: Path) -> Path:
    area = tmp_path / "exec-work"
    area.mkdir()
    return area


@pytest.fixture
def ctl(area: Path) -> Iterator[FakeExecutorCtl]:
    fake = FakeExecutorCtl(area, command=(sys.executable, FAKE_EXECUTOR, "sleep", "30"))
    yield fake
    fake.close()


@pytest.fixture
def runner(ctl: FakeExecutorCtl, area: Path) -> ExecutorCtlRunner:
    return ExecutorCtlRunner(ctl.socket_path, area, work_group=os.getgid())


def prepared(runner: ExecutorCtlRunner) -> uuid.UUID:
    attempt_id = uuid.uuid7()
    runner.create_work_dir(attempt_id)
    return attempt_id


def wait_exit(runner: ExecutorCtlRunner, attempt_id: uuid.UUID) -> None:
    deadline = time.monotonic() + WAIT_TIMEOUT_S
    while runner.status(attempt_id).is_running:
        assert time.monotonic() < deadline, "executor não terminou a tempo"
        time.sleep(0.05)


# Área de trabalho --------------------------------------------------------------------------


def test_area_da_tentativa_e_o_id_dentro_da_raiz(runner: ExecutorCtlRunner, area: Path) -> None:
    attempt_id = uuid.uuid7()

    assert runner.work_dir(attempt_id) == area / str(attempt_id)


def test_area_nasce_vazia_com_o_layout_e_as_permissoes_do_contrato(
    runner: ExecutorCtlRunner,
) -> None:
    attempt_id = uuid.uuid7()

    work_dir = runner.create_work_dir(attempt_id)

    assert sorted(entry.name for entry in work_dir.iterdir()) == ["inputs", "out"]
    modes = {
        path.name: stat.S_IMODE(path.stat().st_mode)
        for path in (work_dir, work_dir / "inputs", work_dir / "out")
    }
    assert modes == {str(attempt_id): 0o2770, "inputs": 0o2750, "out": 0o2770}
    assert work_dir.stat().st_gid == os.getgid()


def test_areas_presentes_ignoram_lost_found_e_nomes_que_nao_sao_tentativa(
    runner: ExecutorCtlRunner, area: Path
) -> None:
    attempt_id = prepared(runner)
    (area / "lost+found").mkdir()
    (area / "lixo").mkdir()

    assert runner.list_work() == [attempt_id]


# start ------------------------------------------------------------------------------------


def test_start_envia_a_imagem_fixada_e_devolve_o_executor_rodando(
    runner: ExecutorCtlRunner, ctl: FakeExecutorCtl
) -> None:
    attempt_id = prepared(runner)

    status = runner.start(attempt_id, IMAGE)

    assert status.phase is ExecutorPhase.RUNNING
    assert ctl.last_start_body == {"image": IMAGE}


def test_start_repetido_adota_o_container_existente(
    runner: ExecutorCtlRunner, ctl: FakeExecutorCtl
) -> None:
    attempt_id = prepared(runner)
    runner.start(attempt_id, IMAGE)
    first = ctl.containers[str(attempt_id)].process

    status = runner.start(attempt_id, IMAGE)

    assert status.is_running
    assert ctl.containers[str(attempt_id)].process is first
    assert ctl.operations(attempt_id) == ["start", "start"]


@pytest.mark.parametrize(
    ("arrange", "code"),
    [
        ("image", "image_not_allowed"),
        ("dirty", "work_area_dirty"),
        ("missing", "work_dir_missing"),
        ("docker", "docker_unavailable"),
    ],
)
def test_start_recusado_vira_erro_do_runner_com_o_codigo_do_ctl(
    runner: ExecutorCtlRunner, ctl: FakeExecutorCtl, area: Path, arrange: str, code: str
) -> None:
    attempt_id = prepared(runner)
    image = IMAGE
    match arrange:
        case "image":
            image = OTHER_IMAGE
        case "dirty":
            (area / str(uuid.uuid7())).mkdir()
        case "missing":
            attempt_id = uuid.uuid7()
        case "docker":
            ctl.docker_down = True

    with pytest.raises(ExecutorCtlError) as error:
        runner.start(attempt_id, image)

    assert error.value.code == code
    assert str(attempt_id) not in ctl.containers


# status -----------------------------------------------------------------------------------


def test_status_de_tentativa_sem_container_e_absent(runner: ExecutorCtlRunner) -> None:
    status = runner.status(uuid.uuid7())

    assert status.phase is ExecutorPhase.ABSENT
    assert not status.ran


def test_status_de_executor_terminado_traz_codigo_e_oom(
    runner: ExecutorCtlRunner, ctl: FakeExecutorCtl
) -> None:
    attempt_id = uuid.uuid7()
    ctl.containers[str(attempt_id)] = FakeContainer("exited", exit_code=137, oom_killed=True)

    status = runner.status(attempt_id)

    assert (status.phase, status.exit_code, status.oom_killed) == (ExecutorPhase.EXITED, 137, True)
    assert not status.timed_out


def test_container_criado_e_ainda_nao_iniciado_conta_como_vivo(
    runner: ExecutorCtlRunner, ctl: FakeExecutorCtl
) -> None:
    # Só o fim confirmado libera reagendar: `created` pode virar `running` a qualquer momento.
    attempt_id = uuid.uuid7()
    ctl.containers[str(attempt_id)] = FakeContainer("created")

    assert runner.status(attempt_id).is_running


def test_status_fora_do_contrato_e_recusado(
    runner: ExecutorCtlRunner, ctl: FakeExecutorCtl
) -> None:
    attempt_id = uuid.uuid7()
    ctl.containers[str(attempt_id)] = FakeContainer("paused")

    with pytest.raises(ExecutorCtlError) as error:
        runner.status(attempt_id)

    assert error.value.code == "invalid_response"


# stop -------------------------------------------------------------------------------------


def test_stop_encerra_o_executor_e_devolve_o_estado_final(
    runner: ExecutorCtlRunner,
) -> None:
    attempt_id = prepared(runner)
    runner.start(attempt_id, IMAGE)

    status = runner.stop(attempt_id, timedelta(seconds=1))

    assert status.phase is ExecutorPhase.EXITED
    assert not runner.status(attempt_id).is_running


def test_stop_limita_a_carencia_a_10_segundos(
    runner: ExecutorCtlRunner, ctl: FakeExecutorCtl
) -> None:
    attempt_id = uuid.uuid7()
    ctl.unstoppable = True
    ctl.containers[str(attempt_id)] = FakeContainer("running")

    status = runner.stop(attempt_id, timedelta(minutes=1))

    # O falso recusa (400) carência acima de 10 s: chegar aqui prova o teto no cliente.
    assert status.is_running


def test_stop_do_que_nao_existe_e_sucesso(runner: ExecutorCtlRunner) -> None:
    assert runner.stop(uuid.uuid7(), timedelta(seconds=1)).phase is ExecutorPhase.ABSENT


# remove -----------------------------------------------------------------------------------


def test_remove_apaga_container_e_area_e_repetir_e_sucesso(
    runner: ExecutorCtlRunner, ctl: FakeExecutorCtl
) -> None:
    attempt_id = prepared(runner)
    runner.start(attempt_id, IMAGE)
    runner.stop(attempt_id, timedelta(seconds=1))

    runner.remove(attempt_id)
    runner.remove(attempt_id)

    assert not runner.work_dir(attempt_id).exists()
    assert runner.status(attempt_id).phase is ExecutorPhase.ABSENT
    assert ctl.operations(attempt_id).count("remove") == 2


def test_remove_de_executor_vivo_e_recusado(
    runner: ExecutorCtlRunner, ctl: FakeExecutorCtl
) -> None:
    attempt_id = prepared(runner)
    runner.start(attempt_id, IMAGE)

    with pytest.raises(ExecutorCtlError) as error:
        runner.remove(attempt_id)

    assert error.value.code == "still_running"
    assert runner.work_dir(attempt_id).exists()


# ctl indisponível -------------------------------------------------------------------------


@pytest.mark.parametrize("operation", ["start", "status", "stop", "remove"])
def test_ctl_fora_do_ar_vira_indisponivel_em_toda_operacao(
    runner: ExecutorCtlRunner, ctl: FakeExecutorCtl, operation: str
) -> None:
    attempt_id = prepared(runner)
    ctl.close()

    with pytest.raises(ExecutorCtlError) as error:
        _call(runner, operation, attempt_id)

    assert error.value.code == "unavailable"


@pytest.mark.parametrize("operation", ["start", "status", "stop", "remove"])
def test_ctl_que_nao_responde_a_tempo_vira_indisponivel(
    ctl: FakeExecutorCtl, area: Path, operation: str
) -> None:
    runner = ExecutorCtlRunner(
        ctl.socket_path,
        area,
        work_group=os.getgid(),
        call_timeout=SHORT_TIMEOUT,
        start_timeout=SHORT_TIMEOUT,
    )
    attempt_id = prepared(runner)
    ctl.response_delay_s = 1.5
    started = time.monotonic()

    with pytest.raises(ExecutorCtlError) as error:
        _call(runner, operation, attempt_id)

    assert error.value.code == "unavailable"
    assert time.monotonic() - started < ctl.response_delay_s


def test_executor_real_roda_ate_o_fim_pelo_ctl(area: Path) -> None:
    ctl = FakeExecutorCtl(area, command=(sys.executable, FAKE_EXECUTOR, "exit", "3"))
    try:
        runner = ExecutorCtlRunner(ctl.socket_path, area, work_group=os.getgid())
        attempt_id = prepared(runner)
        runner.start(attempt_id, IMAGE)
        wait_exit(runner, attempt_id)

        status = runner.status(attempt_id)
    finally:
        ctl.close()

    assert (status.phase, status.exit_code) == (ExecutorPhase.EXITED, 3)


def _call(runner: ExecutorCtlRunner, operation: str, attempt_id: uuid.UUID) -> None:
    match operation:
        case "start":
            runner.start(attempt_id, IMAGE)
        case "status":
            runner.status(attempt_id)
        case "stop":
            runner.stop(attempt_id, timedelta(seconds=1))
        case "remove":
            runner.remove(attempt_id)
        case _:
            raise AssertionError(operation)
