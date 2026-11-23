"""Docker de teste que só conta: mede quantas chamadas ao CLI cada operação do serviço faz."""

from executor_ctl.docker_cli import ContainerStatus, DockerCli, State


class CountingDocker(DockerCli):
    """Docker sem processo: devolve o estado pedido pelo teste e conta cada chamada ao CLI."""

    def __init__(self, state: State) -> None:
        super().__init__(binary="docker-que-nao-roda")
        self.state: State = state
        self.calls: list[str] = []

    def inspect(self, name: str) -> ContainerStatus:
        self.calls.append("inspect")
        if self.state == "absent":
            return ContainerStatus.absent()
        exit_code = 0 if self.state == "exited" else None
        return ContainerStatus(self.state, exit_code, False, None, None)

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
