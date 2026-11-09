"""Sonda da imagem do executor: cliente, ciclo e fiação na manutenção, sem banco e sem Docker."""

import logging
import os
import threading
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from goblinshift.entrypoints.maintenance import (
    ExecutorImageProbe,
    executor_image_cycles,
    run_cycles,
    serve,
)
from goblinshift.platform.settings import Settings
from goblinshift.runs.worker.executor_ctl_runner import ExecutorCtlError, ExecutorCtlRunner
from goblinshift.runs.worker.runner import LocalRunner
from tests.runs.fake_executor_ctl import IMAGE, FakeExecutorCtl

OTHER_IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "b" * 64
SHORT_TIMEOUT = timedelta(seconds=0.2)
SLOW_RESPONSE_S = 1.0
OK = {"executor_image_missing": 0, "executor_image_probe_failed": 0}
MISSING = {"executor_image_missing": 1, "executor_image_probe_failed": 0}
FAILED = {"executor_image_missing": 0, "executor_image_probe_failed": 1}

Setup = Callable[[FakeExecutorCtl], None]


@pytest.fixture
def area(tmp_path: Path) -> Path:
    area = tmp_path / "exec-work"
    area.mkdir()
    return area


@pytest.fixture
def ctl(area: Path) -> Iterator[FakeExecutorCtl]:
    fake = FakeExecutorCtl(area)
    yield fake
    fake.close()


@pytest.fixture
def runner(ctl: FakeExecutorCtl, area: Path) -> ExecutorCtlRunner:
    return ExecutorCtlRunner(ctl.socket_path, area, work_group=os.getgid())


def nothing(_ctl: FakeExecutorCtl) -> None:
    pass


def image_gone(ctl: FakeExecutorCtl) -> None:
    ctl.present_images = ()


def docker_down(ctl: FakeExecutorCtl) -> None:
    ctl.docker_down = True


def old_ctl(ctl: FakeExecutorCtl) -> None:
    ctl.image_route_installed = False


def not_allowed(ctl: FakeExecutorCtl) -> None:
    ctl.allowed_images = (OTHER_IMAGE,)


def wrong_type(ctl: FakeExecutorCtl) -> None:
    ctl.image_response = {"present": "sim"}


def extra_key(ctl: FakeExecutorCtl) -> None:
    ctl.image_response = {"present": True, "detail": "x"}


def socket_gone(ctl: FakeExecutorCtl) -> None:
    ctl.close()


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        (nothing, OK),
        (image_gone, MISSING),
        (docker_down, FAILED),
        (old_ctl, FAILED),
        (not_allowed, FAILED),
        (wrong_type, FAILED),
        (extra_key, FAILED),
        (socket_gone, FAILED),
    ],
    ids=lambda value: value.__name__ if callable(value) else "",
)
def test_contagens_da_sonda_seguem_a_resposta_do_ctl(
    ctl: FakeExecutorCtl,
    runner: ExecutorCtlRunner,
    setup: Setup,
    expected: dict[str, int],
) -> None:
    setup(ctl)

    assert ExecutorImageProbe(runner.image_present, IMAGE).run_cycle() == expected


def test_ctl_que_nao_responde_no_prazo_conta_como_sonda_falha(
    ctl: FakeExecutorCtl, area: Path
) -> None:
    ctl.response_delay_s = SLOW_RESPONSE_S
    runner = ExecutorCtlRunner(
        ctl.socket_path, area, work_group=os.getgid(), call_timeout=SHORT_TIMEOUT
    )

    assert ExecutorImageProbe(runner.image_present, IMAGE).run_cycle() == FAILED


def test_defeito_inesperado_na_sonda_nao_sai_do_ciclo() -> None:
    def broken(_image: str) -> bool:
        raise TypeError("defeito")

    assert ExecutorImageProbe(broken, IMAGE).run_cycle() == FAILED


def test_sonda_pergunta_pela_imagem_configurada_com_a_ref_crua(
    ctl: FakeExecutorCtl, runner: ExecutorCtlRunner
) -> None:
    ExecutorImageProbe(runner.image_present, IMAGE).run_cycle()

    assert ctl.calls == [("image", IMAGE)]


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        (docker_down, "docker_unavailable"),
        (old_ctl, "not_found"),
        (not_allowed, "image_not_allowed"),
        (wrong_type, "invalid_response"),
        (socket_gone, "unavailable"),
    ],
    ids=lambda value: value.__name__ if callable(value) else str(value),
)
def test_cliente_nunca_transforma_falha_em_ausente(
    ctl: FakeExecutorCtl, runner: ExecutorCtlRunner, setup: Setup, code: str
) -> None:
    setup(ctl)

    with pytest.raises(ExecutorCtlError) as caught:
        runner.image_present(IMAGE)

    assert caught.value.code == code


def probe_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "goblinshift.maintenance"]


def test_imagem_ausente_registra_o_evento_de_alerta_com_a_ref(
    ctl: FakeExecutorCtl, runner: ExecutorCtlRunner, caplog: pytest.LogCaptureFixture
) -> None:
    image_gone(ctl)

    with caplog.at_level(logging.INFO, logger="goblinshift.maintenance"):
        ExecutorImageProbe(runner.image_present, IMAGE).run_cycle()

    [record] = probe_records(caplog)
    assert record.levelno == logging.ERROR
    assert getattr(record, "fields") == {"alert": "executor_image_missing", "image": IMAGE}  # noqa: B009


def test_sonda_falha_registra_o_codigo_do_motivo_sem_traceback(
    ctl: FakeExecutorCtl, runner: ExecutorCtlRunner, caplog: pytest.LogCaptureFixture
) -> None:
    docker_down(ctl)

    with caplog.at_level(logging.INFO, logger="goblinshift.maintenance"):
        ExecutorImageProbe(runner.image_present, IMAGE).run_cycle()

    [record] = probe_records(caplog)
    assert record.levelno == logging.WARNING
    assert record.exc_info is None
    assert getattr(record, "fields") == {  # noqa: B009
        "alert": "executor_image_probe_failed",
        "error": "docker_unavailable",
    }


def test_imagem_presente_nao_gera_linha_de_log(
    runner: ExecutorCtlRunner, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="goblinshift.maintenance"):
        ExecutorImageProbe(runner.image_present, IMAGE).run_cycle()

    assert not probe_records(caplog)


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def provider(exporter: InMemorySpanExporter) -> TracerProvider:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider


@pytest.mark.parametrize(
    ("setup", "missing", "failed"),
    [(image_gone, 1, 0), (docker_down, 0, 1), (old_ctl, 0, 1), (socket_gone, 0, 1)],
    ids=lambda value: value.__name__ if callable(value) else "",
)
def test_docker_ou_ctl_fora_nao_viram_ciclo_falho_no_span(
    ctl: FakeExecutorCtl,
    runner: ExecutorCtlRunner,
    provider: TracerProvider,
    exporter: InMemorySpanExporter,
    setup: Setup,
    missing: int,
    failed: int,
) -> None:
    setup(ctl)
    probe = ExecutorImageProbe(runner.image_present, IMAGE)

    run_cycles(provider.get_tracer("t"), (("imagem do executor", probe.run_cycle),))

    [span] = exporter.get_finished_spans()
    attributes = dict(span.attributes or {})
    assert attributes["maintenance.executor_image_missing"] == missing
    assert attributes["maintenance.executor_image_probe_failed"] == failed
    assert attributes["maintenance.failed_cycles"] == 0
    assert span.status.status_code is StatusCode.UNSET


def probe_settings(tmp_path: Path, runner: str = "executor_ctl") -> Settings:
    return Settings.model_validate(
        {
            "environment": "test",
            "runner": runner,
            "executor_image": IMAGE,
            # Porta sem ninguém ouvindo: toda conexão ao banco é recusada na hora.
            "db_port": 1,
            "storage_root": tmp_path / "storage",
        }
    )


def test_runner_local_nao_tem_daemon_a_sondar(tmp_path: Path) -> None:
    local = LocalRunner(tmp_path, attempt_timeout=timedelta(seconds=1))

    assert executor_image_cycles(probe_settings(tmp_path, "local"), local) == ()


def test_executor_ctl_sonda_a_imagem_da_configuracao(
    tmp_path: Path, ctl: FakeExecutorCtl, runner: ExecutorCtlRunner
) -> None:
    [(name, cycle)] = executor_image_cycles(probe_settings(tmp_path), runner)

    assert (name, cycle()) == ("imagem do executor", OK)
    assert ctl.calls == [("image", IMAGE)]


class OneTurn(threading.Event):
    """Pede a parada na espera entre as voltas: `serve` roda exatamente uma."""

    def wait(self, timeout: float | None = None) -> bool:
        self.set()
        return True


def test_serve_sonda_a_imagem_mesmo_com_o_banco_fora(
    tmp_path: Path,
    ctl: FakeExecutorCtl,
    runner: ExecutorCtlRunner,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="goblinshift.maintenance"):
        serve(probe_settings(tmp_path), runner, OneTurn())

    assert ctl.calls == [("image", IMAGE)]
    failed = [r.getMessage() for r in probe_records(caplog)]
    assert failed == ["ciclo de manutenção falhou", "ciclo de manutenção falhou"]
