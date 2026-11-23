"""Docker de teste que só conta: mede quantas chamadas ao CLI cada operação do serviço faz."""

import uuid

from executor_ctl.docker_cli import ContainerStatus, DockerCli, State

from .conftest import owned_status


class CountingDocker(DockerCli):
    """Docker sem processo: devolve o estado pedido pelo teste e conta cada chamada ao CLI.

    O container é sempre o da tentativa `attempt_id` (label e imagem certas), para o serviço o
    aceitar como seu.
    """

    def __init__(self, state: State, attempt_id: uuid.UUID) -> None:
        super().__init__(binary="docker-que-nao-roda")
        self.state: State = state
        self.attempt_id = attempt_id
        self.calls: list[str] = []

    def inspect(self, name: str) -> ContainerStatus:
        self.calls.append("inspect")
        return owned_status(self.state, self.attempt_id)

    def run(self, arguments: list[str]) -> None:
        self.calls.append("run")
        self.state = "running"

    def stop(self, name: str, grace_s: int) -> None:
        # Como o `docker stop`: o que roda termina; o que nunca iniciou continua `created`.
        self.calls.append("stop")
        if self.state == "running":
            self.state = "exited"

    def remove(self, name: str) -> None:
        self.calls.append("rm")
        self.state = "absent"
