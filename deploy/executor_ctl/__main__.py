"""Ponto de entrada do serviço: `python3 -m executor_ctl [caminho da configuração]`."""

import grp
import logging
import os
import signal
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import FrameType

from .config import DEFAULT_CONFIG_PATH, Config, ConfigError, load_config
from .docker_cli import DockerCli
from .journal import configure_logging
from .mounts import mount_options, read_self_mountinfo
from .server import bind_server
from .service import ExecutorService
from .witness import PassiveObserver

EXIT_CONFIG_ERROR = 2
EXIT_HOST_NOT_READY = 1
REQUIRED_AREA_OPTIONS = frozenset({"nosuid", "nodev", "noexec"})

_log = logging.getLogger("executor_ctl")


def main(
    argv: list[str] | None = None,
    *,
    observer_provider: Callable[[Config], PassiveObserver] | None = None,
    read_mountinfo: Callable[[], str] = read_self_mountinfo,
) -> int:
    configure_logging()
    arguments = sys.argv[1:] if argv is None else argv
    config_path = Path(arguments[0]) if arguments else DEFAULT_CONFIG_PATH
    try:
        config = load_config(config_path)
    except ConfigError as error:
        _log.error("configuração inválida", extra={"detail": str(error)})
        return EXIT_CONFIG_ERROR
    if config.require_mount and not _work_area_ready(config.work_area, read_mountinfo):
        return EXIT_HOST_NOT_READY
    group_id = _group_id(config.group)
    socket_group_id = _group_id(config.socket_group)
    if group_id is None or socket_group_id is None:
        return EXIT_HOST_NOT_READY
    if socket_group_id == group_id:
        # Dois nomes no mesmo GID (`groupadd -o`): o executor voltaria a alcançar o socket (D11).
        _log.error("grupo do socket com o GID do executor", extra={"detail": str(group_id)})
        return EXIT_HOST_NOT_READY

    observer = None
    if observer_provider is not None:
        try:
            observer = observer_provider(config)
        except Exception:
            # Operator capability errors must not echo arbitrary payloads/secrets.
            return EXIT_HOST_NOT_READY
        if not isinstance(observer, PassiveObserver):
            return EXIT_HOST_NOT_READY
    service = (
        ExecutorService(config, DockerCli(), group_id)
        if observer is None
        else ExecutorService(config, DockerCli(), group_id, observer=observer)
    )
    server = bind_server(config, service, socket_group_id)

    def shut_down(signum: int, frame: FrameType | None) -> None:
        # shutdown() espera o laço do serve_forever, que roda nesta mesma thread.
        threading.Thread(target=server.shutdown).start()

    signal.signal(signal.SIGTERM, shut_down)
    _log.info("pronto", extra={"detail": f"{config.socket} (runtime {config.runtime})"})
    try:
        server.serve_forever()
    finally:
        server.server_close()
    _log.info("encerrado")
    return 0


def _work_area_ready(work_area: Path, read_mountinfo: Callable[[], str]) -> bool:
    if not os.path.ismount(work_area):
        # Sem o filesystem de 6 GiB, uma tentativa encheria o disco raiz da VPS (ADR 005).
        _log.error("área de trabalho não montada", extra={"detail": str(work_area)})
        return False
    if not mount_options(read_mountinfo(), work_area).issuperset(REQUIRED_AREA_OPTIONS):
        # Um arquivo escrito pelo sandbox não vira binário, setuid nem dispositivo no host (D12).
        _log.error("área montada sem nosuid,nodev,noexec", extra={"detail": str(work_area)})
        return False
    return True


def _group_id(name: str) -> int | None:
    try:
        return grp.getgrnam(name).gr_gid
    except KeyError:
        _log.error("grupo inexistente", extra={"detail": name})
        return None


if __name__ == "__main__":
    sys.exit(main())
