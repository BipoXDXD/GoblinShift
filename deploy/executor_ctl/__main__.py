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
from .server import bind_server
from .service import ExecutorService
from .witness import PassiveObserver

EXIT_CONFIG_ERROR = 2
EXIT_HOST_NOT_READY = 1

_log = logging.getLogger("executor_ctl")


def main(
    argv: list[str] | None = None,
    *,
    observer_provider: Callable[[Config], PassiveObserver] | None = None,
) -> int:
    configure_logging()
    arguments = sys.argv[1:] if argv is None else argv
    config_path = Path(arguments[0]) if arguments else DEFAULT_CONFIG_PATH
    try:
        config = load_config(config_path)
    except ConfigError as error:
        _log.error("configuração inválida", extra={"detail": str(error)})
        return EXIT_CONFIG_ERROR
    if config.require_mount and not os.path.ismount(config.work_area):
        # Sem o filesystem de 6 GiB, uma tentativa encheria o disco raiz da VPS (ADR 005).
        _log.error("área de trabalho não montada", extra={"detail": str(config.work_area)})
        return EXIT_HOST_NOT_READY
    try:
        group_id = grp.getgrnam(config.group).gr_gid
    except KeyError:
        _log.error("grupo inexistente", extra={"detail": config.group})
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
    server = bind_server(config, service, group_id)

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


if __name__ == "__main__":
    sys.exit(main())
