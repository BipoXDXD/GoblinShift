"""Docker de teste que trava dentro do lock da área, para provar o prazo do lock."""

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from executor_ctl.docker_cli import ContainerStatus, DockerCli

# Teto de espera dos eventos do teste: só estoura se o serviço travar de verdade.
EVENT_TIMEOUT_S = 10.0


class StuckDocker(DockerCli):
    """Docker sem processo cujo primeiro `inspect` trava até o teste soltar."""

    def __init__(self) -> None:
        super().__init__(binary="docker-que-nao-roda")
        self.stuck = threading.Event()
        self.released = threading.Event()
        self.calls: list[str] = []

    def inspect(self, name: str) -> ContainerStatus:
        self.calls.append(f"inspect {name}")
        if not self.stuck.is_set():
            self.stuck.set()
            assert self.released.wait(EVENT_TIMEOUT_S)
        return ContainerStatus.absent()

    def run(self, arguments: list[str]) -> None:
        self.calls.append("run")

    def remove(self, name: str) -> None:
        self.calls.append(f"rm {name}")


@contextmanager
def holding_area(docker: StuckDocker, operation: Callable[[], object]) -> Iterator[None]:
    """Roda `operation` noutra thread e só cede quando ela está dentro do lock, travada."""
    thread = threading.Thread(target=operation)
    thread.start()
    try:
        assert docker.stuck.wait(EVENT_TIMEOUT_S), "a operação à frente não tomou o lock"
        yield
    finally:
        docker.released.set()
        thread.join(EVENT_TIMEOUT_S)
        assert not thread.is_alive()
