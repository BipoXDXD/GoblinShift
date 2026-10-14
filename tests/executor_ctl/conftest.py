"""Servidor do executor-ctl num socket temporário, para os testes de protocolo e integração."""

import grp
import json
import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from executor_ctl.config import Config
from executor_ctl.docker_cli import DockerCli
from executor_ctl.server import bind_server
from executor_ctl.service import ExecutorService


def current_group() -> str:
    """Grupo primário de quem roda o teste; no host, o equivalente é o gs-exec."""
    return grp.getgrgid(os.getgid()).gr_name


def make_config(
    tmp_path: Path, socket_dir: Path, *, runtime: str = "runc", allowed: list[str] | None = None
) -> Config:
    area = tmp_path / "exec-work"
    area.mkdir(exist_ok=True)
    images_file = tmp_path / "images.json"
    images_file.write_text(json.dumps({"allowed": allowed or []}))
    return Config(
        socket=socket_dir / "ctl.sock",
        group=current_group(),
        work_area=area,
        runtime=runtime,
        images_file=images_file,
        require_mount=False,
    )


@pytest.fixture
def socket_dir() -> Iterator[Path]:
    # O caminho de um socket Unix tem teto de ~104 bytes no macOS; o tmp_path passa disso.
    with tempfile.TemporaryDirectory(prefix="gs-ctl-") as directory:
        yield Path(directory)


# Curto para o teste do cliente lento não esperar o prazo de produção.
TEST_READ_TIMEOUT_S = 0.5
# O padrão de 0,5 s do serve_forever atrasaria o encerramento do servidor em cada teste.
POLL_INTERVAL_S = 0.01


@contextmanager
def running_server(config: Config, docker: DockerCli) -> Iterator[Path]:
    group_id = grp.getgrnam(config.group).gr_gid
    service = ExecutorService(config, docker, group_id)
    server = bind_server(config, service, group_id, read_timeout_s=TEST_READ_TIMEOUT_S)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": POLL_INTERVAL_S}, daemon=True
    )
    thread.start()
    try:
        yield config.socket
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
